"""Persistent PI-01D3 workflow for deterministic read-only investigation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from soc_agent.contracts import (
    InvestigationEvidence,
    ServiceRequestContext,
    SocAgentActionAdapterDescriptor,
    SocAgentChatRequest,
    SocEnrichmentActionAttempt,
    SocEnrichmentAdapterProvenanceContract,
    SocEnrichmentAttemptStatus,
    SocEnrichmentCompositionConfig,
    SocEnrichmentExecution,
    SocEnrichmentExecutionCommand,
    SocEnrichmentExecutionStatus,
    SocEnrichmentExecutionTrigger,
    SocEnrichmentPlannedAction,
    SocEnrichmentPlanStatus,
    SocEnrichmentReplayCommand,
    SocEnrichmentResultMode,
    SocEnrichmentWorkflowResult,
)
from soc_agent.protocols import (
    AlertRepository,
    InvestigationEvidenceRepository,
    SocActionAdapterRegistryPort,
    SocEnrichmentExecutionRepository,
)
from soc_agent.utils.hashing import stable_hash

from .enrichment import SocEnrichmentPlanner
from .errors import (
    SocEnrichmentWorkflowBusyError,
    SocEnrichmentWorkflowConflictError,
    SocEnrichmentWorkflowError,
    SocEnrichmentWorkflowPersistenceError,
)
from .service import SocAgentActionDispatcher, SocAgentCapabilityRouter

_TERMINAL_SUCCESS_ATTEMPTS = {
    SocEnrichmentAttemptStatus.SUCCESS,
    SocEnrichmentAttemptStatus.NOT_FOUND,
}
_TERMINAL_NONRETRYABLE_ATTEMPTS = {
    SocEnrichmentAttemptStatus.CONTRACT_FAILED,
    SocEnrichmentAttemptStatus.DENIED,
}
_NONRETRYABLE_PROVIDER_ERROR_MARKERS = (
    "notfound",
    "not_found",
    "configuration",
    "validation",
    "schema",
    "permission",
    "authentication",
)


class SocInvestigationWorkflowService:
    """Plan and execute allowlisted actions against an existing immutable run."""

    def __init__(
        self,
        *,
        run_repository: AlertRepository,
        execution_repository: SocEnrichmentExecutionRepository,
        evidence_repository: InvestigationEvidenceRepository,
        action_adapter_registry: SocActionAdapterRegistryPort,
        composition: SocEnrichmentCompositionConfig,
        selected_descriptors: list[SocAgentActionAdapterDescriptor],
    ) -> None:
        if not composition.enabled or composition.required_result_mode is None:
            raise ValueError("persistent investigation workflow requires enabled composition")
        self._run_repository = run_repository
        self._execution_repository = execution_repository
        self._evidence_repository = evidence_repository
        self._registry = action_adapter_registry
        self._composition = composition
        self._planner = SocEnrichmentPlanner(composition.policy)
        self._dispatcher = SocAgentActionDispatcher(
            action_adapter_registry=action_adapter_registry,
            evidence_repository=None,
        )
        self._descriptors = {(descriptor.route, descriptor.action): descriptor for descriptor in selected_descriptors}
        if len(self._descriptors) != len(selected_descriptors):
            raise ValueError("persistent investigation workflow requires unique route/action descriptors")
        self._composition_hash = stable_hash(composition.model_dump(mode="json"))

    def execute(
        self,
        command: SocEnrichmentExecutionCommand | Mapping[str, Any],
        *,
        context: ServiceRequestContext,
    ) -> SocEnrichmentWorkflowResult:
        execution_command = SocEnrichmentExecutionCommand.model_validate(command)
        if execution_command.trigger is SocEnrichmentExecutionTrigger.REPLAY:
            raise ValueError("use replay() to create a linked replay execution")
        return self._execute(
            execution_command,
            context=context,
            replay_of_execution_id=None,
            replay_reason=None,
        )

    def replay(
        self,
        command: SocEnrichmentReplayCommand | Mapping[str, Any],
        *,
        context: ServiceRequestContext,
    ) -> SocEnrichmentWorkflowResult:
        replay_command = SocEnrichmentReplayCommand.model_validate(command)
        source = self._execution_repository.get_enrichment_execution(replay_command.execution_id)
        if source is None:
            raise SocEnrichmentWorkflowError(f"enrichment execution {replay_command.execution_id} not found")
        if context.idempotency_key == source.idempotency_key:
            raise SocEnrichmentWorkflowConflictError("replay requires a new idempotency key")
        return self._execute(
            SocEnrichmentExecutionCommand(
                run_id=source.run_id,
                thread_id=source.thread_id,
                trigger=SocEnrichmentExecutionTrigger.REPLAY,
            ),
            context=context,
            replay_of_execution_id=source.execution_id,
            replay_reason=replay_command.reason,
        )

    def get(self, execution_id: str) -> SocEnrichmentWorkflowResult | None:
        execution = self._execution_repository.get_enrichment_execution(execution_id)
        if execution is None:
            return None
        return self._result(
            execution,
            idempotent_replay=False,
            provider_invocation_count=0,
        )

    def _execute(
        self,
        command: SocEnrichmentExecutionCommand,
        *,
        context: ServiceRequestContext,
        replay_of_execution_id: str | None,
        replay_reason: str | None,
    ) -> SocEnrichmentWorkflowResult:
        idempotency_key = _required_idempotency_key(context)
        run = self._run_repository.get_run(command.run_id)
        if run is None:
            raise SocEnrichmentWorkflowError(f"analysis run {command.run_id} not found")
        plan = self._planner.plan(run, thread_id=command.thread_id)
        now = _utc_now()
        initial_status, completed_at = _initial_execution_status(plan.status, now=now)
        execution = SocEnrichmentExecution(
            execution_id=_execution_id(idempotency_key),
            idempotency_key=idempotency_key,
            trigger=command.trigger,
            run_id=run.run_id,
            alert_id=run.alert_id,
            thread_id=command.thread_id,
            plan=plan,
            composition_hash=self._composition_hash,
            required_result_mode=self._composition.required_result_mode,
            status=initial_status,
            replay_of_execution_id=replay_of_execution_id,
            replay_reason=replay_reason,
            request_id=context.request_id,
            trace_id=context.trace_id,
            actor_id=context.actor.actor_id,
            created_at=now,
            updated_at=now,
            completed_at=completed_at,
        )
        created = self._execution_repository.create_enrichment_execution(execution)
        if not created:
            existing = self._execution_repository.find_enrichment_execution_by_idempotency_key(idempotency_key)
            if existing is None:
                raise SocEnrichmentWorkflowPersistenceError("enrichment execution identity conflicted without readable state")
            self._validate_existing(
                existing,
                command=command,
                replay_of_execution_id=replay_of_execution_id,
            )
            return self._resume(existing, context=context)
        if execution.status is not SocEnrichmentExecutionStatus.RUNNING:
            return self._result(
                execution,
                idempotent_replay=False,
                provider_invocation_count=0,
            )
        return self._run_claimed(execution, context=context)

    def _validate_existing(
        self,
        execution: SocEnrichmentExecution,
        *,
        command: SocEnrichmentExecutionCommand,
        replay_of_execution_id: str | None,
    ) -> None:
        expected = (
            execution.run_id == command.run_id
            and execution.thread_id == command.thread_id
            and execution.trigger is command.trigger
            and execution.replay_of_execution_id == replay_of_execution_id
            and execution.composition_hash == self._composition_hash
        )
        if not expected:
            raise SocEnrichmentWorkflowConflictError("enrichment idempotency key is already bound to different execution semantics")

    def _resume(
        self,
        execution: SocEnrichmentExecution,
        *,
        context: ServiceRequestContext,
    ) -> SocEnrichmentWorkflowResult:
        if execution.status in {
            SocEnrichmentExecutionStatus.COMPLETED,
            SocEnrichmentExecutionStatus.NO_ACTIONS,
            SocEnrichmentExecutionStatus.BLOCKED,
            SocEnrichmentExecutionStatus.FAILED,
        }:
            return self._result(
                execution,
                idempotent_replay=True,
                provider_invocation_count=0,
            )
        if execution.status is SocEnrichmentExecutionStatus.RUNNING:
            stale_at = execution.updated_at + timedelta(seconds=self._composition.retry_policy.stale_after_seconds)
            if _utc_now() < stale_at:
                raise SocEnrichmentWorkflowBusyError(f"enrichment execution {execution.execution_id} is already running")
            execution = self._claim(execution)
            self._recover_interrupted_attempts(execution)
        elif execution.status is SocEnrichmentExecutionStatus.RETRYABLE_FAILED:
            execution = self._claim(execution)
        else:  # pragma: no cover - exhaustive enum guard
            raise SocEnrichmentWorkflowError(f"unsupported enrichment execution status {execution.status.value}")
        return self._run_claimed(execution, context=context)

    def _claim(self, execution: SocEnrichmentExecution) -> SocEnrichmentExecution:
        now = _utc_now()
        claimed = execution.model_copy(
            update={
                "status": SocEnrichmentExecutionStatus.RUNNING,
                "retryable": False,
                "completed_at": None,
                "updated_at": now,
                "version": execution.version + 1,
            }
        )
        if not self._execution_repository.compare_and_set_enrichment_execution(
            claimed,
            expected_version=execution.version,
        ):
            raise SocEnrichmentWorkflowBusyError(f"enrichment execution {execution.execution_id} was claimed concurrently")
        return claimed

    def _recover_interrupted_attempts(
        self,
        execution: SocEnrichmentExecution,
    ) -> None:
        attempts = self._execution_repository.list_enrichment_action_attempts(execution.execution_id)
        for attempt in attempts:
            if attempt.status is not SocEnrichmentAttemptStatus.RUNNING:
                continue
            evidence_id = _evidence_id(execution.execution_id, attempt.plan_action_id)
            evidence = self._evidence_repository.get_evidence(evidence_id)
            if evidence is not None:
                recovered = attempt.model_copy(
                    update={
                        "status": _successful_attempt_status(
                            attempt.route,
                            evidence.result_payload,
                        ),
                        "version": attempt.version + 1,
                        "provider_invoked": True,
                        "result_mode": (SocEnrichmentResultMode.MOCK if evidence.mocked else SocEnrichmentResultMode.REAL),
                        "evidence_id": evidence.evidence_id,
                        "result_hash": stable_hash(evidence.result_payload),
                        "ended_at": _utc_now(),
                    }
                )
            else:
                recovered = attempt.model_copy(
                    update={
                        "status": SocEnrichmentAttemptStatus.INTERRUPTED,
                        "version": attempt.version + 1,
                        "retryable": (attempt.attempt_number < self._composition.retry_policy.max_attempts_per_action),
                        "error_type": "InterruptedProviderInvocation",
                        "error": "provider invocation did not leave persisted evidence",
                        "ended_at": _utc_now(),
                    }
                )
            if not self._execution_repository.compare_and_set_enrichment_action_attempt(
                recovered,
                expected_version=attempt.version,
            ):
                raise SocEnrichmentWorkflowBusyError(f"enrichment attempt {attempt.attempt_id} was recovered concurrently")

    def _run_claimed(
        self,
        execution: SocEnrichmentExecution,
        *,
        context: ServiceRequestContext,
    ) -> SocEnrichmentWorkflowResult:
        invocations = 0
        latest = _latest_attempts(self._execution_repository.list_enrichment_action_attempts(execution.execution_id))
        for planned in execution.plan.actions:
            previous = latest.get(planned.action_id)
            if previous is not None and previous.status in (_TERMINAL_SUCCESS_ATTEMPTS | _TERMINAL_NONRETRYABLE_ATTEMPTS):
                continue
            if previous is not None and previous.attempt_number >= self._composition.retry_policy.max_attempts_per_action:
                continue
            attempt = self._start_attempt(
                execution,
                planned,
                attempt_number=(previous.attempt_number + 1 if previous is not None else 1),
                context=context,
            )
            completed = self._invoke_action(
                execution,
                planned,
                attempt,
                context=context,
            )
            latest[planned.action_id] = completed
            if completed.provider_invoked:
                invocations += 1

        finalized = self._finalize_execution(execution)
        return self._result(
            finalized,
            idempotent_replay=False,
            provider_invocation_count=invocations,
        )

    def _start_attempt(
        self,
        execution: SocEnrichmentExecution,
        planned: SocEnrichmentPlannedAction,
        *,
        attempt_number: int,
        context: ServiceRequestContext,
    ) -> SocEnrichmentActionAttempt:
        descriptor = self._descriptor(planned)
        key = _action_idempotency_key(
            execution.execution_id,
            planned.action_id,
            attempt_number,
        )
        attempt = SocEnrichmentActionAttempt(
            attempt_id=_attempt_id(key),
            execution_id=execution.execution_id,
            plan_action_id=planned.action_id,
            attempt_number=attempt_number,
            action_idempotency_key=key,
            route=planned.route,
            action=planned.action,
            adapter_id=descriptor.adapter_id,
            request_id=context.request_id,
            trace_id=context.trace_id,
        )
        if not self._execution_repository.create_enrichment_action_attempt(attempt):
            raise SocEnrichmentWorkflowBusyError(f"enrichment action {planned.action_id} attempt {attempt_number} already exists")
        return attempt

    def _invoke_action(
        self,
        execution: SocEnrichmentExecution,
        planned: SocEnrichmentPlannedAction,
        attempt: SocEnrichmentActionAttempt,
        *,
        context: ServiceRequestContext,
    ) -> SocEnrichmentActionAttempt:
        descriptor = self._descriptor(planned)
        action_context = context.model_copy(update={"idempotency_key": attempt.action_idempotency_key})
        request = _chat_request(
            execution,
            planned,
        )
        router = SocAgentCapabilityRouter(allowed_routes={planned.route})
        route_decision = router.route(request)
        permission = self._dispatcher.check_permission(
            request,
            route_decision,
            context=action_context,
        )
        result = self._dispatcher.dispatch(
            request,
            route_decision,
            context=action_context,
            permission_decision=permission,
        )
        now = _utc_now()
        if result.status == "denied":
            completed = attempt.model_copy(
                update={
                    "status": SocEnrichmentAttemptStatus.DENIED,
                    "version": attempt.version + 1,
                    "provider_invoked": False,
                    "error_type": "ActionDenied",
                    "error": _bounded_error(result.message),
                    "ended_at": now,
                }
            )
            return self._complete_attempt(attempt, completed)
        if result.status == "failed":
            error_type = _result_error_type(result.payload)
            retryable = _provider_failure_retryable(error_type, result.message) and attempt.attempt_number < self._composition.retry_policy.max_attempts_per_action
            completed = attempt.model_copy(
                update={
                    "status": SocEnrichmentAttemptStatus.PROVIDER_FAILED,
                    "version": attempt.version + 1,
                    "provider_invoked": True,
                    "retryable": retryable,
                    "error_type": error_type,
                    "error": _bounded_error(result.message),
                    "ended_at": now,
                }
            )
            return self._complete_attempt(attempt, completed)

        try:
            result_mode = _result_mode(descriptor, result.payload)
            if result_mode is not execution.required_result_mode:
                raise ValueError(f"required {execution.required_result_mode.value} result but adapter returned {result_mode.value}")
        except ValueError as exc:
            completed = attempt.model_copy(
                update={
                    "status": SocEnrichmentAttemptStatus.CONTRACT_FAILED,
                    "version": attempt.version + 1,
                    "provider_invoked": True,
                    "error_type": "ResultModeContractError",
                    "error": _bounded_error(str(exc)),
                    "ended_at": now,
                }
            )
            return self._complete_attempt(attempt, completed)

        evidence = InvestigationEvidence(
            evidence_id=_evidence_id(execution.execution_id, planned.action_id),
            route=result.route,
            action=result.action,
            status=result.status,
            message=result.message,
            result_payload=result.payload,
            mocked=result_mode is SocEnrichmentResultMode.MOCK,
            run_id=execution.run_id,
            alert_id=execution.alert_id,
            thread_id=execution.thread_id,
            source_proposal_id=planned.action_id,
            context_hash=execution.plan.input_hash,
            request_id=context.request_id,
            trace_id=context.trace_id,
            actor=context.actor,
        )
        self._evidence_repository.save_evidence(evidence)
        completed = attempt.model_copy(
            update={
                "status": _successful_attempt_status(planned.route, result.payload),
                "version": attempt.version + 1,
                "provider_invoked": True,
                "result_mode": result_mode,
                "evidence_id": evidence.evidence_id,
                "result_hash": stable_hash(result.payload),
                "ended_at": now,
            }
        )
        return self._complete_attempt(attempt, completed)

    def _complete_attempt(
        self,
        previous: SocEnrichmentActionAttempt,
        completed: SocEnrichmentActionAttempt,
    ) -> SocEnrichmentActionAttempt:
        if not self._execution_repository.compare_and_set_enrichment_action_attempt(
            completed,
            expected_version=previous.version,
        ):
            raise SocEnrichmentWorkflowPersistenceError(f"enrichment attempt {previous.attempt_id} completion conflicted")
        return completed

    def _finalize_execution(
        self,
        execution: SocEnrichmentExecution,
    ) -> SocEnrichmentExecution:
        attempts = self._execution_repository.list_enrichment_action_attempts(execution.execution_id)
        latest = _latest_attempts(attempts)
        latest_values = list(latest.values())
        success_count = sum(item.status is SocEnrichmentAttemptStatus.SUCCESS for item in latest_values)
        not_found_count = sum(item.status is SocEnrichmentAttemptStatus.NOT_FOUND for item in latest_values)
        failed = [item for item in latest_values if item.status not in _TERMINAL_SUCCESS_ATTEMPTS]
        retryable = any(item.retryable and item.attempt_number < self._composition.retry_policy.max_attempts_per_action for item in failed)
        if not failed and len(latest_values) == len(execution.plan.actions):
            status = SocEnrichmentExecutionStatus.COMPLETED
        elif retryable:
            status = SocEnrichmentExecutionStatus.RETRYABLE_FAILED
        else:
            status = SocEnrichmentExecutionStatus.FAILED
        last_failed = failed[-1] if failed else None
        now = _utc_now()
        finalized = execution.model_copy(
            update={
                "status": status,
                "version": execution.version + 1,
                "attempt_count": len(attempts),
                "success_count": success_count,
                "not_found_count": not_found_count,
                "failed_count": len(failed),
                "evidence_count": success_count + not_found_count,
                "retryable": retryable,
                "last_error_type": (last_failed.error_type if last_failed is not None else None),
                "last_error": last_failed.error if last_failed is not None else None,
                "updated_at": now,
                "completed_at": now,
            }
        )
        if not self._execution_repository.compare_and_set_enrichment_execution(
            finalized,
            expected_version=execution.version,
        ):
            raise SocEnrichmentWorkflowPersistenceError(f"enrichment execution {execution.execution_id} finalization conflicted")
        return finalized

    def _descriptor(
        self,
        planned: SocEnrichmentPlannedAction,
    ) -> SocAgentActionAdapterDescriptor:
        descriptor = self._descriptors.get((planned.route, planned.action))
        if descriptor is None:
            raise SocEnrichmentWorkflowConflictError(f"persisted plan route {planned.route!r} is not bound by current composition")
        return descriptor

    def _result(
        self,
        execution: SocEnrichmentExecution,
        *,
        idempotent_replay: bool,
        provider_invocation_count: int,
    ) -> SocEnrichmentWorkflowResult:
        attempts = self._execution_repository.list_enrichment_action_attempts(execution.execution_id)
        return SocEnrichmentWorkflowResult(
            execution=execution,
            attempts=attempts,
            idempotent_replay=idempotent_replay,
            provider_invocation_count=provider_invocation_count,
            evidence_persisted_count=execution.evidence_count,
        )


def _required_idempotency_key(context: ServiceRequestContext) -> str:
    value = context.idempotency_key
    if value is None or not value.strip():
        raise ValueError("persistent investigation requires context.idempotency_key")
    return value.strip()


def _initial_execution_status(
    plan_status: SocEnrichmentPlanStatus,
    *,
    now: datetime,
) -> tuple[SocEnrichmentExecutionStatus, datetime | None]:
    if plan_status is SocEnrichmentPlanStatus.NO_ACTIONS:
        return SocEnrichmentExecutionStatus.NO_ACTIONS, now
    if plan_status is SocEnrichmentPlanStatus.BLOCKED:
        return SocEnrichmentExecutionStatus.BLOCKED, now
    return SocEnrichmentExecutionStatus.RUNNING, None


def _chat_request(
    execution: SocEnrichmentExecution,
    planned: SocEnrichmentPlannedAction,
) -> SocAgentChatRequest:
    payload = dict(planned.payload)
    payload["context_refs"] = {
        "alert_id": execution.alert_id,
        "run_id": execution.run_id,
        "thread_id": execution.thread_id,
        "proposal_id": planned.action_id,
        "enrichment_action_id": planned.action_id,
    }
    return SocAgentChatRequest(
        message=f"Run persisted read-only investigation action {planned.route}",
        thread_id=execution.thread_id,
        run_id=execution.run_id,
        allowed_routes=[planned.route],
        metadata={
            "soc_route": planned.route,
            "action_payload": payload,
            "orchestrator_action_origin": "planned",
            "enrichment_action_id": planned.action_id,
            "enrichment_execution_id": execution.execution_id,
        },
    )


def _result_mode(
    descriptor: SocAgentActionAdapterDescriptor,
    payload: Mapping[str, Any],
) -> SocEnrichmentResultMode:
    try:
        contract = SocEnrichmentAdapterProvenanceContract(descriptor.metadata.get("result_provenance_contract"))
    except (TypeError, ValueError) as exc:  # startup validation should prevent this
        raise ValueError("adapter result provenance contract is missing or invalid") from exc
    if contract is SocEnrichmentAdapterProvenanceContract.MOCK_ONLY:
        return SocEnrichmentResultMode.MOCK
    if contract is SocEnrichmentAdapterProvenanceContract.REAL_ONLY:
        return SocEnrichmentResultMode.REAL

    result_body = _result_body(payload)
    mocked = result_body.get("mocked")
    if not isinstance(mocked, bool):
        raise ValueError("runtime-declared adapter result must expose boolean mocked")
    return SocEnrichmentResultMode.MOCK if mocked else SocEnrichmentResultMode.REAL


def _successful_attempt_status(
    route: str,
    payload: Mapping[str, Any],
) -> SocEnrichmentAttemptStatus:
    body = _result_body(payload)
    if route == "asset.lookup" and body.get("asset_found") is False:
        return SocEnrichmentAttemptStatus.NOT_FOUND
    if route == "asset.locate" and body.get("found") is False:
        return SocEnrichmentAttemptStatus.NOT_FOUND
    if route == "threat_intel.ip_reputation.lookup" and body.get("reputation_found") is False:
        return SocEnrichmentAttemptStatus.NOT_FOUND
    if route == "security_tag.lookup":
        if body.get("lookup_status") == "not_found":
            return SocEnrichmentAttemptStatus.NOT_FOUND
        if "lookup_status" not in body and body.get("security_tag_found") is False and body.get("provider_records_found") in {None, False}:
            return SocEnrichmentAttemptStatus.NOT_FOUND
    return SocEnrichmentAttemptStatus.SUCCESS


def _result_body(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = payload.get("mcp_result")
    return nested if isinstance(nested, Mapping) else payload


def _result_error_type(payload: Mapping[str, Any]) -> str:
    value = payload.get("error_type")
    return str(value)[:256] if value else "ProviderExecutionError"


def _provider_failure_retryable(error_type: str, message: str) -> bool:
    normalized = f"{error_type} {message}".casefold()
    return not any(marker in normalized for marker in _NONRETRYABLE_PROVIDER_ERROR_MARKERS)


def _latest_attempts(
    attempts: list[SocEnrichmentActionAttempt],
) -> dict[str, SocEnrichmentActionAttempt]:
    latest: dict[str, SocEnrichmentActionAttempt] = {}
    for attempt in attempts:
        current = latest.get(attempt.plan_action_id)
        if current is None or attempt.attempt_number > current.attempt_number:
            latest[attempt.plan_action_id] = attempt
    return latest


def _execution_id(idempotency_key: str) -> str:
    return f"EEXEC-{stable_hash({'idempotency_key': idempotency_key})[:20].upper()}"


def _attempt_id(action_idempotency_key: str) -> str:
    return f"EATT-{stable_hash({'idempotency_key': action_idempotency_key})[:20].upper()}"


def _action_idempotency_key(
    execution_id: str,
    plan_action_id: str,
    attempt_number: int,
) -> str:
    return f"enrichment:{execution_id}:{plan_action_id}:{attempt_number}"


def _evidence_id(execution_id: str, plan_action_id: str) -> str:
    return f"EVI-{stable_hash({'execution_id': execution_id, 'action_id': plan_action_id})[:20].upper()}"


def _bounded_error(value: str) -> str:
    return " ".join(value.split())[:1000] or "unknown enrichment failure"


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "SocEnrichmentWorkflowBusyError",
    "SocEnrichmentWorkflowConflictError",
    "SocEnrichmentWorkflowError",
    "SocEnrichmentWorkflowPersistenceError",
    "SocInvestigationWorkflowService",
]
