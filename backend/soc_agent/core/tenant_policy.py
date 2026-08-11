"""Stable service for post-Runtime tenant disposition policy evaluation."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from soc_agent.contracts import (
    AnalysisRun,
    AnalysisRunStatus,
    AuthorizationMatchResult,
    NormalizationInspectionResult,
    ServiceRequestContext,
    SocEvent,
    SocEventType,
    TenantDispositionPolicy,
    TenantPolicyDecision,
    TenantPolicyEvaluationStatus,
    TenantPolicyTimeSource,
)
from soc_agent.core.authorized_activity import SocAuthorizedActivityService
from soc_agent.core.runtime import inspect_alert_normalization
from soc_agent.protocols import (
    SocEventSink,
    TenantDispositionPolicyResolver,
    TenantPolicyAdvisor,
    TenantPolicyDecisionRepository,
)
from soc_agent.tenant_policy import (
    TenantPolicyDecisionConflictError,
    TenantPolicyNotApplicableError,
    apply_tenant_policy_advisor_result,
    evaluate_tenant_policy,
)


class SocTenantPolicyEvaluationService:
    """Evaluate and persist an independent tenant decision after Runtime."""

    def __init__(
        self,
        *,
        policy_resolver: TenantDispositionPolicyResolver,
        repository: TenantPolicyDecisionRepository,
        environment: str,
        authorized_activity_service: SocAuthorizedActivityService | None = None,
        event_timezone: str | None = None,
        event_sink: SocEventSink | None = None,
        advisor: TenantPolicyAdvisor | None = None,
    ) -> None:
        if not environment.strip():
            raise ValueError("tenant policy environment must be non-empty")
        self._policy_resolver = policy_resolver
        self._repository = repository
        self._environment = environment.strip()
        self._authorized_activity_service = authorized_activity_service
        self._event_timezone = event_timezone
        self._event_sink = event_sink
        self._advisor = advisor

    def observe(
        self,
        run: AnalysisRun,
        *,
        context: ServiceRequestContext,
    ) -> None:
        if run.status not in {
            AnalysisRunStatus.SUCCESS,
            AnalysisRunStatus.NEEDS_REVIEW,
        }:
            return
        self.evaluate(run, context=context)

    def evaluate(
        self,
        run: AnalysisRun,
        *,
        context: ServiceRequestContext,
    ) -> TenantPolicyDecision | None:
        request = run.llm_analysis_request
        inspection = inspect_alert_normalization(run.input_payload) if run.input_payload is not None else None
        policy_time, policy_time_source = _resolve_policy_time(
            inspection.alert.event.event_time if inspection is not None else None,
            event_timezone=self._event_timezone,
        )
        policy = self._policy_resolver.resolve(
            tenant_id=request.tenant_id if request else None,
            environment=self._environment,
            evaluated_at=policy_time,
        )
        if policy is None:
            return None

        authorization_result = self._authorization_result(
            policy,
            run,
            inspection=inspection,
        )
        try:
            decision = evaluate_tenant_policy(
                policy,
                run,
                environment=self._environment,
                authorization_result=authorization_result,
                triggered_by=context.actor,
                policy_time=policy_time,
                policy_time_source=policy_time_source,
            )
        except TenantPolicyNotApplicableError:
            return None
        if decision.evaluation_status is TenantPolicyEvaluationStatus.NO_MATCH and self._advisor is not None:
            decision = apply_tenant_policy_advisor_result(
                decision,
                self._advisor.advise(policy, run),
            )

        existing = self._repository.find_tenant_policy_decision_by_key(decision.decision_key)
        if existing is not None:
            return existing
        try:
            self._repository.save_tenant_policy_decision(decision)
        except TenantPolicyDecisionConflictError:
            existing = self._repository.find_tenant_policy_decision_by_key(decision.decision_key)
            if existing is None:
                raise
            return existing

        if self._event_sink is not None:
            self._event_sink.emit(
                SocEvent(
                    event_type=SocEventType.TENANT_POLICY_DECISION_RECORDED,
                    request_id=context.request_id,
                    actor=decision.evaluated_by,
                    payload={
                        "decision_id": decision.decision_id,
                        "run_id": decision.run_id,
                        "alert_id": decision.alert_id,
                        "tenant_id": decision.tenant_id,
                        "policy_id": decision.policy_id,
                        "policy_version": decision.policy_version,
                        "evaluation_status": decision.evaluation_status.value,
                        "policy_mode": decision.policy_mode.value,
                        "shadow_only": decision.shadow_only,
                        "auto_apply_allowed": decision.auto_apply_allowed,
                    },
                )
            )
        return decision

    def _authorization_result(
        self,
        policy: TenantDispositionPolicy,
        run: AnalysisRun,
        *,
        inspection: NormalizationInspectionResult | None,
    ) -> AuthorizationMatchResult | None:
        needs_authorization = any(rule.enabled and rule.match.authorization_statuses for rule in policy.rules)
        if not needs_authorization or self._authorized_activity_service is None:
            return None
        if inspection is None:
            return None
        return self._authorized_activity_service.match_alert(
            inspection.alert,
            entities=run.entities or inspection.entities,
            fact_reconstruction=run.fact_reconstruction,
            tenant_id=policy.tenant_id,
            environment=self._environment,
            event_timezone=self._event_timezone,
        )

    def get(self, decision_id: str) -> TenantPolicyDecision | None:
        return self._repository.get_tenant_policy_decision(decision_id)

    def list(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        tenant_id: str | None = None,
        policy_id: str | None = None,
        limit: int = 100,
    ) -> list[TenantPolicyDecision]:
        return self._repository.list_tenant_policy_decisions(
            run_id=run_id,
            alert_id=alert_id,
            tenant_id=tenant_id,
            policy_id=policy_id,
            limit=limit,
        )


__all__ = ["SocTenantPolicyEvaluationService"]


def _resolve_policy_time(
    value: datetime | None,
    *,
    event_timezone: str | None,
) -> tuple[datetime | None, TenantPolicyTimeSource]:
    if value is None:
        return None, TenantPolicyTimeSource.EVALUATION_TIME_FALLBACK
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value, TenantPolicyTimeSource.ALERT_EVENT_TIME
    if not event_timezone:
        return None, TenantPolicyTimeSource.EVALUATION_TIME_FALLBACK
    try:
        timezone = ZoneInfo(event_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown tenant policy event timezone: {event_timezone}") from exc
    return (
        value.replace(tzinfo=timezone),
        TenantPolicyTimeSource.ALERT_EVENT_TIME_TIMEZONE_ASSUMED,
    )
