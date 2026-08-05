"""Read-only telemetry and analyst addenda over durable investigation state."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime
from math import ceil

from soc_agent.contracts import (
    AnalysisRun,
    InvestigationEvidence,
    SocEnrichmentActionAttempt,
    SocEnrichmentAttemptStatus,
    SocEnrichmentExecution,
    SocEnrichmentExecutionStatus,
    SocEnrichmentResultMode,
    SocInvestigationAddendum,
    SocInvestigationAddendumItem,
    SocInvestigationRouteTelemetry,
    SocInvestigationShadowReport,
)
from soc_agent.protocols import (
    AlertRepository,
    InvestigationEvidenceRepository,
    SocEnrichmentExecutionRepository,
)
from soc_agent.utils.hashing import stable_hash

_SUCCESS_STATUSES = {
    SocEnrichmentAttemptStatus.SUCCESS,
    SocEnrichmentAttemptStatus.NOT_FOUND,
}


class SocInvestigationReportingError(RuntimeError):
    """Raised when persisted investigation sources cannot form a valid report."""


class SocInvestigationReportingService:
    """Project durable D3 state without invoking Providers or mutating Runtime state."""

    def __init__(
        self,
        *,
        run_repository: AlertRepository,
        execution_repository: SocEnrichmentExecutionRepository,
        evidence_repository: InvestigationEvidenceRepository,
    ) -> None:
        self._run_repository = run_repository
        self._execution_repository = execution_repository
        self._evidence_repository = evidence_repository

    def get_shadow_report(
        self,
        execution_id: str,
    ) -> SocInvestigationShadowReport | None:
        execution = self._execution_repository.get_enrichment_execution(execution_id)
        if execution is None:
            return None
        attempts = self._execution_repository.list_enrichment_action_attempts(execution_id)
        return self._build_shadow_report(execution, attempts)

    def get_report_bundle(
        self,
        execution_id: str,
    ) -> tuple[SocInvestigationShadowReport, SocInvestigationAddendum] | None:
        execution = self._execution_repository.get_enrichment_execution(execution_id)
        if execution is None:
            return None
        attempts = self._execution_repository.list_enrichment_action_attempts(execution_id)
        report = self._build_shadow_report(execution, attempts)
        run = self._run_repository.get_run(execution.run_id)
        if run is None:
            raise SocInvestigationReportingError(f"investigation execution {execution.execution_id} references missing run {execution.run_id}")
        return report, self._build_addendum(
            run=run,
            execution=execution,
            attempts=attempts,
            report=report,
        )

    def get_addendum(
        self,
        execution_id: str,
    ) -> SocInvestigationAddendum | None:
        bundle = self.get_report_bundle(execution_id)
        return bundle[1] if bundle is not None else None

    def list_addenda_for_run(
        self,
        run_id: str,
        *,
        limit: int = 10,
    ) -> list[SocInvestigationAddendum]:
        addenda: list[SocInvestigationAddendum] = []
        for execution in self._execution_repository.list_enrichment_executions(
            run_id=run_id,
            limit=limit,
        ):
            addendum = self.get_addendum(execution.execution_id)
            if addendum is not None:
                addenda.append(addendum)
        return addenda

    def _build_shadow_report(
        self,
        execution: SocEnrichmentExecution,
        attempts: list[SocEnrichmentActionAttempt],
    ) -> SocInvestigationShadowReport:
        attempts_by_action = _attempts_by_action(attempts)
        evidence_by_attempt, evidence_reference_count, evidence_gaps = self._validated_evidence(
            execution,
            attempts,
        )
        source_hash = stable_hash(
            {
                "execution": execution.model_dump(mode="json"),
                "attempts": [item.model_dump(mode="json") for item in attempts],
                "validated_evidence_hashes": sorted(
                    (
                        item.evidence_id,
                        stable_hash(item.model_dump(mode="json")),
                    )
                    for item in evidence_by_attempt.values()
                ),
            }
        )
        latencies = _attempt_latencies_ms(attempts)
        terminal = execution.status is not SocEnrichmentExecutionStatus.RUNNING
        final_items = [_latest_attempt(attempts_by_action.get(action.action_id, [])) for action in execution.plan.actions]
        success_count = sum(item is not None and item.status is SocEnrichmentAttemptStatus.SUCCESS for item in final_items)
        not_found_count = sum(item is not None and item.status is SocEnrichmentAttemptStatus.NOT_FOUND for item in final_items)
        failed_count = sum(item is not None and item.status not in _SUCCESS_STATUSES for item in final_items)
        if terminal:
            failed_count += sum(item is None for item in final_items)

        measurement_gaps = ["provider_cost_not_measured"]
        if latencies:
            measurement_gaps.append("provider_network_latency_not_isolated_from_action_latency")
        else:
            measurement_gaps.append("action_attempt_latency_not_measured")
        if not execution.plan.actions:
            measurement_gaps.append("evidence_coverage_not_applicable_no_actions")
        measurement_gaps.extend(evidence_gaps)
        expected_counters = {
            "attempt_count": len(attempts),
            "success_count": success_count,
            "not_found_count": not_found_count,
            "failed_count": failed_count,
            "evidence_count": evidence_reference_count,
        }
        if any(getattr(execution, key) != value for key, value in expected_counters.items()):
            measurement_gaps.append("execution_counter_projection_mismatch")

        skip_reason_counts = Counter(item.reason_code.value for item in execution.plan.skipped)
        routes = self._route_telemetry(
            execution,
            attempts_by_action=attempts_by_action,
            evidence_by_attempt=evidence_by_attempt,
        )
        persisted_evidence_count = len({item.evidence_id for item in evidence_by_attempt.values()})
        missing_evidence_count = evidence_reference_count - persisted_evidence_count
        planned_action_count = len(execution.plan.actions)
        return SocInvestigationShadowReport(
            report_id=f"ISHR-{source_hash[:16].upper()}",
            source_hash=source_hash,
            execution_id=execution.execution_id,
            run_id=execution.run_id,
            alert_id=execution.alert_id,
            thread_id=execution.thread_id,
            trigger=execution.trigger,
            execution_status=execution.status,
            plan_id=execution.plan.plan_id,
            plan_status=execution.plan.status,
            policy_version=execution.plan.policy_version,
            required_result_mode=execution.required_result_mode,
            source_updated_at=execution.updated_at,
            execution_duration_ms=_duration_ms(execution.created_at, execution.completed_at),
            planned_action_count=planned_action_count,
            skipped_candidate_count=len(execution.plan.skipped),
            skip_reason_counts=dict(sorted(skip_reason_counts.items())),
            attempt_count=len(attempts),
            retry_count=sum(item.attempt_number > 1 for item in attempts),
            provider_invocation_count=sum(item.provider_invoked for item in attempts),
            success_count=success_count,
            not_found_count=not_found_count,
            failed_count=failed_count,
            evidence_reference_count=evidence_reference_count,
            persisted_evidence_count=persisted_evidence_count,
            missing_evidence_count=missing_evidence_count,
            evidence_coverage_ratio=(persisted_evidence_count / planned_action_count if planned_action_count else 0.0),
            attempt_latency_sample_count=len(latencies),
            attempt_latency_ms_p50=_percentile(latencies, 0.50),
            attempt_latency_ms_p95=_percentile(latencies, 0.95),
            attempt_latency_ms_max=max(latencies) if latencies else None,
            routes=routes,
            measurement_gaps=_unique(measurement_gaps),
        )

    def _route_telemetry(
        self,
        execution: SocEnrichmentExecution,
        *,
        attempts_by_action: dict[str, list[SocEnrichmentActionAttempt]],
        evidence_by_attempt: dict[str, InvestigationEvidence],
    ) -> list[SocInvestigationRouteTelemetry]:
        grouped_actions: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        for action in execution.plan.actions:
            action_attempts = attempts_by_action.get(action.action_id, [])
            latest = _latest_attempt(action_attempts)
            adapter_id = latest.adapter_id if latest is not None else "not_recorded"
            grouped_actions[(action.route, action.action, adapter_id)].append(action.action_id)

        telemetry: list[SocInvestigationRouteTelemetry] = []
        for (route, action, adapter_id), action_ids in sorted(grouped_actions.items()):
            route_attempts = [attempt for action_id in action_ids for attempt in attempts_by_action.get(action_id, [])]
            latest_attempts = [item for item in (_latest_attempt(attempts_by_action.get(action_id, [])) for action_id in action_ids) if item is not None]
            latencies = _attempt_latencies_ms(route_attempts)
            evidence_refs = {item.evidence_id for item in latest_attempts if item.evidence_id is not None}
            available_refs = {evidence_by_attempt[item.attempt_id].evidence_id for item in latest_attempts if item.attempt_id in evidence_by_attempt}
            telemetry.append(
                SocInvestigationRouteTelemetry(
                    route=route,
                    action=action,
                    adapter_id=adapter_id,
                    planned_action_count=len(action_ids),
                    attempt_count=len(route_attempts),
                    retry_count=sum(item.attempt_number > 1 for item in route_attempts),
                    provider_invocation_count=sum(item.provider_invoked for item in route_attempts),
                    success_count=sum(item.status is SocEnrichmentAttemptStatus.SUCCESS for item in latest_attempts),
                    not_found_count=sum(item.status is SocEnrichmentAttemptStatus.NOT_FOUND for item in latest_attempts),
                    final_failure_count=sum(item.status not in _SUCCESS_STATUSES for item in latest_attempts),
                    provider_failure_attempt_count=sum(item.status is SocEnrichmentAttemptStatus.PROVIDER_FAILED for item in route_attempts),
                    contract_failure_attempt_count=sum(item.status is SocEnrichmentAttemptStatus.CONTRACT_FAILED for item in route_attempts),
                    denied_attempt_count=sum(item.status is SocEnrichmentAttemptStatus.DENIED for item in route_attempts),
                    interrupted_attempt_count=sum(item.status is SocEnrichmentAttemptStatus.INTERRUPTED for item in route_attempts),
                    evidence_reference_count=len(evidence_refs),
                    persisted_evidence_count=len(available_refs),
                    missing_evidence_count=len(evidence_refs.difference(available_refs)),
                    real_result_count=sum(item.result_mode is SocEnrichmentResultMode.REAL for item in latest_attempts),
                    mock_result_count=sum(item.result_mode is SocEnrichmentResultMode.MOCK for item in latest_attempts),
                    attempt_latency_sample_count=len(latencies),
                    attempt_latency_ms_p50=_percentile(latencies, 0.50),
                    attempt_latency_ms_p95=_percentile(latencies, 0.95),
                    attempt_latency_ms_max=max(latencies) if latencies else None,
                    evidence_coverage_ratio=(len(available_refs) / len(action_ids) if action_ids else 0.0),
                )
            )
        return telemetry

    def _validated_evidence(
        self,
        execution: SocEnrichmentExecution,
        attempts: Iterable[SocEnrichmentActionAttempt],
    ) -> tuple[dict[str, InvestigationEvidence], int, list[str]]:
        validated: dict[str, InvestigationEvidence] = {}
        referenced_ids: set[str] = set()
        missing = False
        mismatched = False
        for attempt in attempts:
            if attempt.evidence_id is None:
                continue
            referenced_ids.add(attempt.evidence_id)
            evidence = self._evidence_repository.get_evidence(attempt.evidence_id)
            if evidence is None:
                missing = True
                continue
            if not _evidence_matches(execution, attempt, evidence):
                mismatched = True
                continue
            validated[attempt.attempt_id] = evidence
        gaps: list[str] = []
        if missing:
            gaps.append("referenced_investigation_evidence_missing")
        if mismatched:
            gaps.append("investigation_evidence_reference_mismatch")
        return validated, len(referenced_ids), gaps

    def _build_addendum(
        self,
        *,
        run: AnalysisRun,
        execution: SocEnrichmentExecution,
        attempts: list[SocEnrichmentActionAttempt],
        report: SocInvestigationShadowReport,
    ) -> SocInvestigationAddendum:
        attempts_by_action = _attempts_by_action(attempts)
        evidence_by_attempt, _, _ = self._validated_evidence(execution, attempts)
        items: list[SocInvestigationAddendumItem] = []
        for action in execution.plan.actions:
            action_attempts = attempts_by_action.get(action.action_id, [])
            latest = _latest_attempt(action_attempts)
            evidence = evidence_by_attempt.get(latest.attempt_id) if latest is not None else None
            items.append(
                SocInvestigationAddendumItem(
                    plan_action_id=action.action_id,
                    route=action.route,
                    action=action.action,
                    adapter_id=latest.adapter_id if latest is not None else None,
                    status=latest.status.value if latest is not None else "not_run",
                    attempt_count=len(action_attempts),
                    retry_count=sum(item.attempt_number > 1 for item in action_attempts),
                    provider_invoked=any(item.provider_invoked for item in action_attempts),
                    result_mode=latest.result_mode if latest is not None else None,
                    evidence_id=latest.evidence_id if latest is not None else None,
                    evidence_available=evidence is not None,
                    evidence_summary=evidence.message if evidence is not None else None,
                    latest_attempt_latency_ms=(_duration_ms(latest.started_at, latest.ended_at) if latest is not None else None),
                )
            )
        verdict = None
        if run.decision is not None:
            verdict = run.decision.verdict.value
        elif run.analysis is not None:
            verdict = run.analysis.verdict.value
        evidence_refs = [item.evidence_id for item in items if item.evidence_id is not None and item.evidence_available]
        attention_required = (
            execution.status
            in {
                SocEnrichmentExecutionStatus.RUNNING,
                SocEnrichmentExecutionStatus.BLOCKED,
                SocEnrichmentExecutionStatus.RETRYABLE_FAILED,
                SocEnrichmentExecutionStatus.FAILED,
            }
            or report.failed_count > 0
            or report.missing_evidence_count > 0
        )
        return SocInvestigationAddendum(
            addendum_id=f"IADD-{stable_hash({'report': report.source_hash, 'run_status': run.status.value, 'verdict': verdict})[:16].upper()}",
            source_report_id=report.report_id,
            source_hash=report.source_hash,
            execution_id=execution.execution_id,
            run_id=execution.run_id,
            alert_id=execution.alert_id,
            trigger=execution.trigger,
            execution_status=execution.status,
            source_updated_at=execution.updated_at,
            base_runtime_status=run.status.value,
            base_runtime_verdict=verdict,
            summary=_addendum_summary(report),
            items=items,
            evidence_refs=evidence_refs,
            evidence_coverage_ratio=report.evidence_coverage_ratio,
            analyst_attention_required=attention_required,
            measurement_gaps=report.measurement_gaps,
        )


def _attempts_by_action(
    attempts: Iterable[SocEnrichmentActionAttempt],
) -> dict[str, list[SocEnrichmentActionAttempt]]:
    grouped: dict[str, list[SocEnrichmentActionAttempt]] = defaultdict(list)
    for attempt in attempts:
        grouped[attempt.plan_action_id].append(attempt)
    for values in grouped.values():
        values.sort(key=lambda item: (item.attempt_number, item.started_at, item.attempt_id))
    return dict(grouped)


def _latest_attempt(
    attempts: list[SocEnrichmentActionAttempt],
) -> SocEnrichmentActionAttempt | None:
    return attempts[-1] if attempts else None


def _attempt_latencies_ms(
    attempts: Iterable[SocEnrichmentActionAttempt],
) -> list[float]:
    return [duration for item in attempts if (duration := _duration_ms(item.started_at, item.ended_at)) is not None]


def _duration_ms(started_at: datetime, ended_at: datetime | None) -> float | None:
    if ended_at is None:
        return None
    return max(0.0, round((ended_at - started_at).total_seconds() * 1000, 3))


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _evidence_matches(
    execution: SocEnrichmentExecution,
    attempt: SocEnrichmentActionAttempt,
    evidence: InvestigationEvidence,
) -> bool:
    return (
        evidence.evidence_id == attempt.evidence_id
        and evidence.run_id == execution.run_id
        and evidence.alert_id == execution.alert_id
        and evidence.thread_id == execution.thread_id
        and evidence.source_proposal_id == attempt.plan_action_id
        and evidence.route == attempt.route
        and evidence.action == attempt.action
    )


def _addendum_summary(report: SocInvestigationShadowReport) -> str:
    if report.execution_status is SocEnrichmentExecutionStatus.NO_ACTIONS:
        return "Read-only investigation completed with no eligible actions under the reviewed policy."
    if report.execution_status is SocEnrichmentExecutionStatus.BLOCKED:
        return "Read-only investigation was blocked before Provider execution by the reviewed policy."
    return (
        f"Read-only investigation {report.execution_status.value}: "
        f"{report.success_count} hit, {report.not_found_count} not found, "
        f"{report.failed_count} unresolved or failed; "
        f"{report.persisted_evidence_count}/{report.planned_action_count} planned actions have persisted evidence."
    )


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


__all__ = [
    "SocInvestigationReportingError",
    "SocInvestigationReportingService",
]
