"""Governed post-Runtime decision, disposition, authorization, and execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from soc_agent.automation import (
    automation_policy_hash as compute_automation_policy_hash,
)
from soc_agent.automation import (
    select_automation_rule,
)
from soc_agent.contracts import (
    SOC_MEMORY_RETRIEVAL_ACTIVATION_POLICY_VERSION,
    ActorAuthSource,
    ActorContext,
    ActorType,
    AnalysisContextReferenceKind,
    AnalysisRun,
    AnalysisRunStatus,
    EntrySurface,
    ServiceRequestContext,
    SocActionAuthorizationDecision,
    SocActionAuthorizationMode,
    SocActionAuthorizationRecord,
    SocActionExecutionRecord,
    SocActionExecutionStatus,
    SocAgentActionCommand,
    SocAgentRiskLevel,
    SocAutomationContributorKind,
    SocAutomationContributorRef,
    SocAutomationContributorRole,
    SocAutomationEvaluationResult,
    SocAutomationPolicy,
    SocAutomationPolicyMode,
    SocAutomationRule,
    SocAutomationTargetSelector,
    SocDecisionSnapshot,
    SocDecisionStageEvaluation,
    SocDecisionStageKind,
    SocDecisionStageStatus,
    SocDecisionTransitionKind,
    SocDecisionTransitionRecord,
    SocDispositionTransitionKind,
    SocDispositionTransitionRecord,
    SocMemoryDecisionEffect,
    SocMemoryRecord,
    SocMemoryRecordStatus,
    SocMemoryReviewEffect,
    SocOperationalDisposition,
    TenantPolicyDecision,
    TenantPolicyEvaluationStatus,
    TenantPolicyMode,
    TenantPolicyReviewEffect,
)
from soc_agent.protocols import (
    MemoryRecordRepository,
    SocActionAdapterRegistryPort,
    SocAutomationRepository,
    TenantPolicyDecisionRepository,
)
from soc_agent.utils.hashing import stable_hash

EFFECTIVE_DECISION_POLICY_VERSION = "soc.effective_decision_policy.v2"
EFFECTIVE_DECISION_POLICY_ID = "soc.effective_decision"


class SocAutomationError(ValueError):
    pass


@dataclass(frozen=True)
class _TenantPolicyOutcome:
    after: SocDecisionSnapshot
    disposition: SocOperationalDisposition | None
    stage: SocDecisionStageEvaluation
    contributors: list[SocAutomationContributorRef]
    decision: TenantPolicyDecision | None


class SocAutomationService:
    """Apply a server-owned policy after the fixed Runtime has persisted a run.

    The model may contribute a detection decision, and confirmed Memory may
    contribute an explicitly reviewed directive. Neither is an authorization.
    Authorization is produced only by this service from the versioned policy.
    """

    def __init__(
        self,
        *,
        repository: SocAutomationRepository,
        policy: SocAutomationPolicy | None,
        environment: str,
        memory_repository: MemoryRecordRepository | None = None,
        tenant_policy_repository: TenantPolicyDecisionRepository | None = None,
        tenant_policy_application_enabled: bool = False,
        action_adapter_registry: SocActionAdapterRegistryPort | None = None,
        execute_authorized_actions: bool = False,
        authorization_ttl_seconds: int = 900,
        max_execution_attempts: int = 3,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        if not environment.strip():
            raise ValueError("automation environment must be non-empty")
        if authorization_ttl_seconds <= 0:
            raise ValueError("authorization_ttl_seconds must be positive")
        if max_execution_attempts <= 0:
            raise ValueError("max_execution_attempts must be positive")
        self._repository = repository
        self._policy = policy
        self._environment = environment.strip()
        self._memory_repository = memory_repository
        self._tenant_policy_repository = tenant_policy_repository
        self._tenant_policy_application_enabled = tenant_policy_application_enabled
        self._registry = action_adapter_registry
        self._execute_authorized_actions = execute_authorized_actions
        self._authorization_ttl_seconds = authorization_ttl_seconds
        self._max_execution_attempts = max_execution_attempts
        self._now = now_provider or (lambda: datetime.now(UTC))
        if tenant_policy_application_enabled and tenant_policy_repository is None:
            raise ValueError("tenant policy application requires a decision repository")

    def observe(self, run: AnalysisRun, *, context: ServiceRequestContext) -> None:
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
    ) -> SocAutomationEvaluationResult:
        now = self._now()
        self._validate_run_and_policy(run, now=now)
        base = _decision_snapshot(run)
        memory_after, memory_kind, memory_contributors = self._effective_decision(
            run,
            base,
            now=now,
        )
        memory_stage = SocDecisionStageEvaluation(
            stage=SocDecisionStageKind.MEMORY,
            status=_memory_stage_status(memory_kind, memory_contributors),
            before=base,
            after=memory_after,
            contributors=memory_contributors,
            summary=_memory_stage_summary(memory_kind, memory_contributors),
        )
        tenant_outcome = self._tenant_policy_outcome(run, memory_after)
        transition_kind = _aggregate_transition_kind(
            base,
            tenant_outcome.after,
            memory_kind=memory_kind,
            tenant_status=tenant_outcome.stage.status,
        )
        selected_rule = None
        if self._policy is not None and transition_kind is not SocDecisionTransitionKind.CONFLICTED:
            selected_rule = select_automation_rule(
                self._policy,
                run,
                tenant_outcome.after,
                tenant_policy_rule_id=(tenant_outcome.decision.selected_rule_id if tenant_outcome.decision is not None else None),
                tenant_disposition=tenant_outcome.disposition,
            )
        effective_disposition = _effective_disposition(
            tenant_outcome.disposition,
            policy=self._policy,
            rule=selected_rule,
        )
        contributors = _base_contributors(run)
        contributors.extend(memory_contributors)
        contributors.extend(tenant_outcome.contributors)
        automation_policy_hash = None
        if self._policy is not None:
            automation_policy_hash = compute_automation_policy_hash(self._policy)
            contributors.append(
                SocAutomationContributorRef(
                    kind=SocAutomationContributorKind.SYSTEM_POLICY,
                    role=SocAutomationContributorRole.OBSERVED,
                    ref_id=self._policy.policy_id,
                    version=self._policy.policy_version,
                    content_hash=automation_policy_hash,
                )
            )
        contributors = _dedupe_contributors(contributors)

        stages = [
            SocDecisionStageEvaluation(
                stage=SocDecisionStageKind.BASE,
                status=SocDecisionStageStatus.OBSERVED,
                after=base,
                contributors=_base_contributors(run),
                summary="Immutable Runtime decision before governed post-processing.",
            ),
            memory_stage,
            tenant_outcome.stage,
            SocDecisionStageEvaluation(
                stage=SocDecisionStageKind.EFFECTIVE,
                status=(
                    SocDecisionStageStatus.CONFLICTED
                    if transition_kind is SocDecisionTransitionKind.CONFLICTED
                    else SocDecisionStageStatus.APPLIED
                    if tenant_outcome.after != base or effective_disposition is not None
                    else SocDecisionStageStatus.UNCHANGED
                ),
                before=tenant_outcome.after,
                after=tenant_outcome.after,
                disposition_before=tenant_outcome.disposition,
                disposition_after=effective_disposition,
                source_id=(self._policy.policy_id if selected_rule and self._policy else EFFECTIVE_DECISION_POLICY_ID),
                source_version=(self._policy.policy_version if selected_rule and self._policy else EFFECTIVE_DECISION_POLICY_VERSION),
                selected_rule_id=(selected_rule.rule_id if selected_rule else None),
                contributors=contributors,
                summary="Final governed decision after Memory, tenant policy, and optional automation policy evaluation.",
            ),
        ]
        resolution_hash = stable_hash(
            {
                "contract": EFFECTIVE_DECISION_POLICY_VERSION,
                "tenant_policy_enabled": self._tenant_policy_application_enabled,
                "tenant_policy_decision_key": (tenant_outcome.decision.decision_key if tenant_outcome.decision is not None else None),
                "automation_policy_hash": automation_policy_hash,
            }
        )

        decision_key = stable_hash(
            {
                "run_id": run.run_id,
                "before": base.model_dump(mode="json"),
                "after": tenant_outcome.after.model_dump(mode="json"),
                "effective_disposition": (effective_disposition.value if effective_disposition else None),
                "stages": [stage.model_dump(mode="json") for stage in stages],
                "contributors": [item.model_dump(mode="json") for item in contributors],
                "resolution_hash": resolution_hash,
            }
        )
        decision_transition = self._repository.find_decision_transition_by_key(decision_key)
        idempotent = decision_transition is not None
        if decision_transition is None:
            decision_transition = SocDecisionTransitionRecord(
                transition_key=decision_key,
                run_id=run.run_id,
                alert_id=run.alert_id,
                tenant_id=run.llm_analysis_request.tenant_id,
                before=base,
                after=tenant_outcome.after,
                effective_disposition=effective_disposition,
                transition_kind=transition_kind,
                stages=stages,
                contributors=contributors,
                policy_id=EFFECTIVE_DECISION_POLICY_ID,
                policy_version=EFFECTIVE_DECISION_POLICY_VERSION,
                policy_hash=resolution_hash,
                created_by=context.actor,
                created_at=now,
            )
            self._repository.save_decision_transition(decision_transition)

        disposition = self._disposition_transition(
            run,
            decision_transition,
            selected_rule,
            tenant_policy_decision=tenant_outcome.decision,
            tenant_disposition=tenant_outcome.disposition,
            contributors=contributors,
            context=context,
            now=now,
        )
        authorization = self._authorization(
            run,
            decision_transition,
            disposition,
            selected_rule,
            contributors=contributors,
            now=now,
        )
        execution = None
        if authorization is not None and authorization.decision is SocActionAuthorizationDecision.AUTHORIZED and self._execute_authorized_actions:
            execution = self._execute(authorization, now=now)

        return SocAutomationEvaluationResult(
            decision_transition=decision_transition,
            disposition_transition=disposition,
            authorization=authorization,
            execution=execution,
            selected_rule_id=selected_rule.rule_id if selected_rule else None,
            tenant_policy_decision_id=(tenant_outcome.decision.decision_id if tenant_outcome.decision is not None else None),
            effective_disposition=effective_disposition,
            idempotent=idempotent,
        )

    def _validate_run_and_policy(self, run: AnalysisRun, *, now: datetime) -> None:
        if now.utcoffset() is None:
            raise SocAutomationError("automation clock must be timezone-aware")
        if run.status not in {
            AnalysisRunStatus.SUCCESS,
            AnalysisRunStatus.NEEDS_REVIEW,
        }:
            raise SocAutomationError("automation requires a completed Runtime run")
        if run.decision is None or run.llm_analysis_request is None:
            raise SocAutomationError("automation requires a completed Runtime decision")
        if self._policy is None:
            return
        tenant_id = run.llm_analysis_request.tenant_id
        if tenant_id != self._policy.tenant_id:
            raise SocAutomationError(f"automation policy tenant {self._policy.tenant_id!r} does not match run tenant {tenant_id!r}")
        if self._environment != self._policy.environment:
            raise SocAutomationError("automation policy environment mismatch")
        if not self._policy.valid_from <= now < self._policy.valid_until:
            raise SocAutomationError("automation policy is outside its validity window")

    def _effective_decision(
        self,
        run: AnalysisRun,
        before: SocDecisionSnapshot,
        *,
        now: datetime,
    ) -> tuple[
        SocDecisionSnapshot,
        SocDecisionTransitionKind,
        list[SocAutomationContributorRef],
    ]:
        eligible = self._eligible_memory_directives(run, now=now)
        if not eligible:
            return before, SocDecisionTransitionKind.UNCHANGED, []

        overrides = [item for item in eligible if item[0].decision_directive is not None and item[0].decision_directive.effect is SocMemoryDecisionEffect.OVERRIDE]
        override_verdicts = {item[0].decision_directive.target_verdict for item in overrides if item[0].decision_directive is not None}
        contributors = [item[1] for item in eligible]
        if len(override_verdicts) > 1:
            return (
                before.model_copy(
                    update={
                        "needs_review": True,
                        "policy_version": EFFECTIVE_DECISION_POLICY_VERSION,
                    }
                ),
                SocDecisionTransitionKind.CONFLICTED,
                contributors,
            )

        applicable = overrides
        if not applicable:
            applicable = [item for item in eligible if item[0].decision_directive is not None and item[0].decision_directive.target_verdict is before.verdict]
        if not applicable:
            return before, SocDecisionTransitionKind.UNCHANGED, contributors

        target_verdict = next(iter(override_verdicts)) if override_verdicts else before.verdict
        directives = [item[0].decision_directive for item in applicable]
        directives = [item for item in directives if item is not None]
        needs_review = before.needs_review
        if any(directive.review_effect is SocMemoryReviewEffect.REQUIRE for directive in directives):
            needs_review = True
        elif any(directive.review_effect is SocMemoryReviewEffect.CLEAR for directive in directives):
            needs_review = False
        suggested_actions = [directive.suggested_action for directive in directives if directive.suggested_action]
        after = before.model_copy(
            update={
                "verdict": target_verdict,
                "suggested_action": (suggested_actions[0] if suggested_actions else before.suggested_action),
                "needs_review": needs_review,
                "policy_version": EFFECTIVE_DECISION_POLICY_VERSION,
            }
        )
        kind = SocDecisionTransitionKind.OVERRIDDEN if target_verdict != before.verdict else SocDecisionTransitionKind.REINFORCED
        return after, kind, contributors

    def _eligible_memory_directives(
        self,
        run: AnalysisRun,
        *,
        now: datetime,
    ) -> list[tuple[SocMemoryRecord, SocAutomationContributorRef]]:
        if self._memory_repository is None or run.llm_analysis_request is None:
            return []
        eligible: list[tuple[SocMemoryRecord, SocAutomationContributorRef]] = []
        for item in run.llm_analysis_request.context_catalog:
            if item.kind is not AnalysisContextReferenceKind.CONFIRMED_MEMORY:
                continue
            memory_id = item.metadata.get("memory_id")
            version = item.metadata.get("memory_version")
            score = item.metadata.get("retrieval_score")
            if not isinstance(memory_id, str) or not isinstance(version, int):
                continue
            if not isinstance(score, (int, float)):
                continue
            record = self._memory_repository.get_memory_record(memory_id)
            if not _memory_record_is_active(record, version=version, now=now):
                continue
            assert record is not None
            if item.metadata.get("record_content_hash") != record.content_hash or item.metadata.get("record_facets_hash") != record.facets_hash:
                continue
            directive = record.decision_directive
            if directive is None or float(score) < directive.minimum_match_score:
                continue
            matched_facets = item.metadata.get("matched_facets")
            if not isinstance(matched_facets, dict):
                matched_facets = {}
            if any(key not in matched_facets or not matched_facets[key] for key in directive.required_facet_keys):
                continue
            eligible.append(
                (
                    record,
                    SocAutomationContributorRef(
                        kind=SocAutomationContributorKind.CONFIRMED_MEMORY,
                        role=(SocAutomationContributorRole.OVERRIDES if directive.effect is SocMemoryDecisionEffect.OVERRIDE else SocAutomationContributorRole.SUPPORTS),
                        ref_id=item.context_ref,
                        version=str(record.version),
                        content_hash=record.content_hash,
                        score=float(score),
                        detail=directive.rationale,
                    ),
                )
            )
        return eligible

    def _tenant_policy_outcome(
        self,
        run: AnalysisRun,
        before: SocDecisionSnapshot,
    ) -> _TenantPolicyOutcome:
        if not self._tenant_policy_application_enabled:
            return _TenantPolicyOutcome(
                after=before,
                disposition=None,
                stage=SocDecisionStageEvaluation(
                    stage=SocDecisionStageKind.TENANT_POLICY,
                    status=SocDecisionStageStatus.DISABLED,
                    before=before,
                    after=before,
                    summary="Tenant policy application is disabled by operator configuration.",
                ),
                contributors=[],
                decision=None,
            )
        if self._tenant_policy_repository is None or run.llm_analysis_request is None:
            return _TenantPolicyOutcome(
                after=before,
                disposition=None,
                stage=SocDecisionStageEvaluation(
                    stage=SocDecisionStageKind.TENANT_POLICY,
                    status=SocDecisionStageStatus.NO_INPUT,
                    before=before,
                    after=before,
                    summary="No persisted tenant policy decision is available.",
                ),
                contributors=[],
                decision=None,
            )
        decisions = [
            item
            for item in self._tenant_policy_repository.list_tenant_policy_decisions(
                run_id=run.run_id,
                tenant_id=run.llm_analysis_request.tenant_id,
                limit=100,
            )
            if item.environment == self._environment
        ]
        decision = max(decisions, key=lambda item: (item.created_at, item.decision_id)) if decisions else None
        if decision is None:
            return _TenantPolicyOutcome(
                after=before,
                disposition=None,
                stage=SocDecisionStageEvaluation(
                    stage=SocDecisionStageKind.TENANT_POLICY,
                    status=SocDecisionStageStatus.NO_INPUT,
                    before=before,
                    after=before,
                    summary="No persisted tenant policy decision matches this run and environment.",
                ),
                contributors=[],
                decision=None,
            )
        contributor = SocAutomationContributorRef(
            kind=SocAutomationContributorKind.TENANT_POLICY,
            role=(
                SocAutomationContributorRole.OBSERVED
                if decision.policy_mode is TenantPolicyMode.SHADOW or decision.evaluation_status is TenantPolicyEvaluationStatus.NO_MATCH
                else SocAutomationContributorRole.OVERRIDES
                if decision.recommended_disposition is not None or decision.review_effect is TenantPolicyReviewEffect.CLEAR
                else SocAutomationContributorRole.SUPPORTS
            ),
            ref_id=decision.decision_id,
            version=decision.policy_version,
            content_hash=decision.policy_hash,
            detail=decision.summary,
        )
        common = {
            "stage": SocDecisionStageKind.TENANT_POLICY,
            "before": before,
            "after": before,
            "source_id": decision.policy_id,
            "source_version": decision.policy_version,
            "source_hash": decision.policy_hash,
            "source_decision_id": decision.decision_id,
            "selected_rule_id": decision.selected_rule_id,
            "contributors": [contributor],
            "summary": decision.summary,
        }
        if decision.evaluation_status is TenantPolicyEvaluationStatus.NO_MATCH:
            return _TenantPolicyOutcome(
                after=before,
                disposition=None,
                stage=SocDecisionStageEvaluation(
                    status=SocDecisionStageStatus.NO_MATCH,
                    **common,
                ),
                contributors=[contributor],
                decision=decision,
            )
        if decision.policy_mode is TenantPolicyMode.SHADOW:
            return _TenantPolicyOutcome(
                after=before,
                disposition=None,
                stage=SocDecisionStageEvaluation(
                    status=SocDecisionStageStatus.SHADOW_MATCHED,
                    disposition_after=decision.recommended_disposition,
                    **common,
                ),
                contributors=[contributor],
                decision=decision,
            )
        if not decision.auto_apply_allowed:
            after = before.model_copy(
                update={
                    "needs_review": True,
                    "policy_version": EFFECTIVE_DECISION_POLICY_VERSION,
                }
            )
            return _TenantPolicyOutcome(
                after=after,
                disposition=None,
                stage=SocDecisionStageEvaluation(
                    status=SocDecisionStageStatus.CONFLICTED,
                    after=after,
                    **{key: value for key, value in common.items() if key != "after"},
                ),
                contributors=[contributor],
                decision=decision,
            )

        needs_review = before.needs_review
        if decision.review_effect is TenantPolicyReviewEffect.REQUIRE:
            needs_review = True
        elif decision.review_effect is TenantPolicyReviewEffect.CLEAR:
            needs_review = False
        after = before.model_copy(
            update={
                "suggested_action": decision.suggested_action or before.suggested_action,
                "needs_review": needs_review,
                "policy_version": EFFECTIVE_DECISION_POLICY_VERSION,
            }
        )
        return _TenantPolicyOutcome(
            after=after,
            disposition=decision.recommended_disposition,
            stage=SocDecisionStageEvaluation(
                status=(SocDecisionStageStatus.APPLIED if after != before or decision.recommended_disposition is not None else SocDecisionStageStatus.UNCHANGED),
                after=after,
                disposition_after=decision.recommended_disposition,
                **{key: value for key, value in common.items() if key != "after"},
            ),
            contributors=[contributor],
            decision=decision,
        )

    def _disposition_transition(
        self,
        run: AnalysisRun,
        decision: SocDecisionTransitionRecord,
        rule: SocAutomationRule | None,
        *,
        tenant_policy_decision: TenantPolicyDecision | None,
        tenant_disposition: SocOperationalDisposition | None,
        contributors: list[SocAutomationContributorRef],
        context: ServiceRequestContext,
        now: datetime,
    ) -> SocDispositionTransitionRecord | None:
        automation_disposition = rule.disposition if rule is not None else None
        if automation_disposition is None and tenant_disposition is None:
            return None
        if automation_disposition is not None:
            assert self._policy is not None
            assert rule is not None
            transition_kind = SocDispositionTransitionKind.APPLIED if self._policy.mode is SocAutomationPolicyMode.ENFORCED else SocDispositionTransitionKind.PROPOSED
            before_disposition = tenant_disposition
            after_disposition = automation_disposition
            policy_id = self._policy.policy_id
            policy_version = self._policy.policy_version
            selected_rule_id = rule.rule_id if rule is not None else None
            transition_contributors = [
                *contributors,
                _rule_contributor(self._policy, rule),
            ]
        else:
            assert tenant_policy_decision is not None
            transition_kind = SocDispositionTransitionKind.APPLIED
            before_disposition = None
            after_disposition = tenant_disposition
            policy_id = tenant_policy_decision.policy_id
            policy_version = tenant_policy_decision.policy_version
            selected_rule_id = tenant_policy_decision.selected_rule_id
            transition_contributors = contributors
        key = stable_hash(
            {
                "decision_transition_id": decision.transition_id,
                "before_disposition": (before_disposition.value if before_disposition else None),
                "after_disposition": (after_disposition.value if after_disposition else None),
                "rule_id": selected_rule_id,
                "policy_id": policy_id,
                "policy_version": policy_version,
                "transition_kind": transition_kind.value,
            }
        )
        existing = self._repository.find_disposition_transition_by_key(key)
        if existing is not None:
            return existing
        record = SocDispositionTransitionRecord(
            transition_key=key,
            run_id=run.run_id,
            alert_id=run.alert_id,
            tenant_id=run.llm_analysis_request.tenant_id,
            decision_transition_id=decision.transition_id,
            before=before_disposition,
            after=after_disposition,
            transition_kind=transition_kind,
            contributors=_dedupe_contributors(transition_contributors),
            policy_id=policy_id,
            policy_version=policy_version,
            selected_rule_id=selected_rule_id,
            created_by=context.actor,
            created_at=now,
        )
        self._repository.save_disposition_transition(record)
        return record

    def _authorization(
        self,
        run: AnalysisRun,
        decision: SocDecisionTransitionRecord,
        disposition: SocDispositionTransitionRecord | None,
        rule: SocAutomationRule | None,
        *,
        contributors: list[SocAutomationContributorRef],
        now: datetime,
    ) -> SocActionAuthorizationRecord | None:
        if rule is None or rule.action is None:
            return None
        if self._policy is None:
            raise SocAutomationError("action rule requires an automation policy")
        target = _resolve_action_target(run, rule.action.target_selector)
        descriptor = _find_descriptor(self._registry, rule)
        authorization_decision, reason, risk_level = _authorization_decision(
            self._policy,
            rule,
            replay_of_run_id=run.replay_of_run_id,
            target=target,
            descriptor=descriptor,
        )
        command_payload = dict(rule.action.static_payload)
        if target is not None:
            command_payload[rule.action.target_payload_field] = target
        command_payload["context_refs"] = {
            "run_id": run.run_id,
            "alert_id": run.alert_id,
            "decision_transition_id": decision.transition_id,
            "disposition_transition_id": (disposition.transition_id if disposition is not None else None),
        }
        key = stable_hash(
            {
                "decision_transition_id": decision.transition_id,
                "disposition_transition_id": (disposition.transition_id if disposition is not None else None),
                "rule_id": rule.rule_id,
                "action": rule.action.model_dump(mode="json"),
                "target": target,
                "authorization_decision": authorization_decision.value,
                "policy_version": self._policy.policy_version,
            }
        )
        existing = self._repository.find_action_authorization_by_key(key)
        if existing is not None:
            return existing
        record = SocActionAuthorizationRecord(
            authorization_key=key,
            run_id=run.run_id,
            alert_id=run.alert_id,
            tenant_id=run.llm_analysis_request.tenant_id,
            decision_transition_id=decision.transition_id,
            disposition_transition_id=(disposition.transition_id if disposition is not None else None),
            mode=rule.authorization_mode,
            decision=authorization_decision,
            route=rule.action.route,
            action=rule.action.action,
            adapter_id=rule.action.adapter_id,
            risk_level=risk_level,
            target_type=rule.action.target_selector,
            target_value=target or "<unresolved>",
            command_payload=command_payload,
            reason=reason,
            contributors=_dedupe_contributors([*contributors, _rule_contributor(self._policy, rule)]),
            policy_id=self._policy.policy_id,
            policy_version=self._policy.policy_version,
            selected_rule_id=rule.rule_id,
            expires_at=(now + timedelta(seconds=self._authorization_ttl_seconds) if authorization_decision is SocActionAuthorizationDecision.AUTHORIZED else None),
            authorized_by=_automation_actor(self._policy),
            created_at=now,
        )
        self._repository.save_action_authorization(record)
        return record

    def _execute(
        self,
        authorization: SocActionAuthorizationRecord,
        *,
        now: datetime,
    ) -> SocActionExecutionRecord:
        if self._policy is None:
            raise SocAutomationError("action execution requires an automation policy")
        if self._registry is None:
            raise SocAutomationError("authorized action execution requires an adapter registry")
        prior_attempts = self._repository.list_action_executions(
            authorization_id=authorization.authorization_id,
            limit=self._max_execution_attempts,
        )
        if prior_attempts:
            latest = max(prior_attempts, key=lambda item: item.attempt)
            if latest.status in {
                SocActionExecutionStatus.SUCCEEDED,
                SocActionExecutionStatus.FAILED_TERMINAL,
                SocActionExecutionStatus.SKIPPED,
            }:
                return latest
            if latest.attempt >= self._max_execution_attempts:
                return latest
            attempt = latest.attempt + 1
        else:
            attempt = 1
        execution_key = stable_hash(
            {
                "authorization_id": authorization.authorization_id,
                "authorization_key": authorization.authorization_key,
                "attempt": attempt,
            }
        )
        existing = self._repository.find_action_execution_by_key(execution_key)
        if existing is not None:
            return existing
        idempotency_key = f"soc-automation:{authorization.authorization_key}"
        execution_context = ServiceRequestContext(
            actor=_automation_actor(self._policy),
            trace_id=authorization.run_id,
            idempotency_key=idempotency_key,
        )
        command = SocAgentActionCommand(
            route=authorization.route,
            action=authorization.action,
            dry_run=False,
            payload=authorization.command_payload,
        )
        started_at = now
        if authorization.expires_at is not None and authorization.expires_at <= now:
            status = SocActionExecutionStatus.SKIPPED
            payload = {}
            error_type = "AuthorizationExpired"
            error_message = "Action authorization expired before execution."
        else:
            try:
                self._registry.preflight_execute(command, context=execution_context)
                result = self._registry.execute(command, context=execution_context)
                status = SocActionExecutionStatus.SUCCEEDED if result.status == "success" else SocActionExecutionStatus.FAILED_TERMINAL
                payload = result.model_dump(mode="json")
                error_type = None
                error_message = None
            except (LookupError, ValueError) as exc:
                status = SocActionExecutionStatus.FAILED_TERMINAL
                payload = {}
                error_type = type(exc).__name__
                error_message = str(exc)
            except Exception as exc:  # noqa: BLE001 - external provider failures remain retryable evidence
                status = SocActionExecutionStatus.FAILED_RETRYABLE
                payload = {}
                error_type = type(exc).__name__
                error_message = str(exc)
        record = SocActionExecutionRecord(
            execution_key=execution_key,
            authorization_id=authorization.authorization_id,
            run_id=authorization.run_id,
            alert_id=authorization.alert_id,
            route=authorization.route,
            action=authorization.action,
            adapter_id=authorization.adapter_id,
            status=status,
            attempt=attempt,
            idempotency_key=idempotency_key,
            external_request_id=_optional_string(payload, "external_request_id"),
            external_state_before=_optional_dict(payload, "external_state_before"),
            external_state_after=_optional_dict(payload, "external_state_after"),
            result_payload=payload,
            error_type=error_type,
            error_message=error_message,
            executed_by=execution_context.actor,
            started_at=started_at,
            ended_at=self._now(),
        )
        self._repository.save_action_execution(record)
        return record


def _decision_snapshot(run: AnalysisRun) -> SocDecisionSnapshot:
    decision = run.decision
    assert decision is not None
    return SocDecisionSnapshot(
        verdict=decision.verdict,
        confidence=decision.confidence,
        evidence_state=decision.evidence_state,
        suggested_action=decision.suggested_action,
        needs_review=decision.needs_review,
        policy_version=decision.policy_version,
    )


def _memory_stage_status(
    transition_kind: SocDecisionTransitionKind,
    contributors: list[SocAutomationContributorRef],
) -> SocDecisionStageStatus:
    if transition_kind is SocDecisionTransitionKind.CONFLICTED:
        return SocDecisionStageStatus.CONFLICTED
    if transition_kind is SocDecisionTransitionKind.OVERRIDDEN:
        return SocDecisionStageStatus.OVERRIDDEN
    if transition_kind is SocDecisionTransitionKind.REINFORCED:
        return SocDecisionStageStatus.REINFORCED
    if contributors:
        return SocDecisionStageStatus.NO_MATCH
    return SocDecisionStageStatus.NO_INPUT


def _memory_stage_summary(
    transition_kind: SocDecisionTransitionKind,
    contributors: list[SocAutomationContributorRef],
) -> str:
    if transition_kind is SocDecisionTransitionKind.CONFLICTED:
        return "Conflicting reviewed Memory directives required review and blocked downstream automation."
    if transition_kind is SocDecisionTransitionKind.OVERRIDDEN:
        return "An eligible reviewed Memory directive changed the effective detection state."
    if transition_kind is SocDecisionTransitionKind.REINFORCED:
        return "Eligible reviewed Memory directives reinforced the base detection state."
    if contributors:
        return "Reviewed Memory directives were present but none applied to this base decision."
    return "No eligible reviewed Memory decision directive was present."


def _aggregate_transition_kind(
    before: SocDecisionSnapshot,
    after: SocDecisionSnapshot,
    *,
    memory_kind: SocDecisionTransitionKind,
    tenant_status: SocDecisionStageStatus,
) -> SocDecisionTransitionKind:
    if memory_kind is SocDecisionTransitionKind.CONFLICTED or tenant_status is SocDecisionStageStatus.CONFLICTED:
        return SocDecisionTransitionKind.CONFLICTED
    if before.verdict is not after.verdict:
        return SocDecisionTransitionKind.OVERRIDDEN
    if before != after or memory_kind is SocDecisionTransitionKind.REINFORCED:
        return SocDecisionTransitionKind.REINFORCED
    return SocDecisionTransitionKind.UNCHANGED


def _effective_disposition(
    tenant_disposition: SocOperationalDisposition | None,
    *,
    policy: SocAutomationPolicy | None,
    rule: SocAutomationRule | None,
) -> SocOperationalDisposition | None:
    if policy is not None and policy.mode is SocAutomationPolicyMode.ENFORCED and rule is not None and rule.disposition is not None:
        return rule.disposition
    return tenant_disposition


def _memory_record_is_active(
    record: SocMemoryRecord | None,
    *,
    version: int,
    now: datetime,
) -> bool:
    return bool(
        record is not None
        and record.version == version
        and record.status is SocMemoryRecordStatus.CONFIRMED
        and record.retrieval_enabled
        and record.retrieval_policy_version == SOC_MEMORY_RETRIEVAL_ACTIVATION_POLICY_VERSION
        and record.retrieval_updated_by is not None
        and record.retrieval_updated_at is not None
        and bool(record.retrieval_reason)
        and record.retrieval_valid_until is not None
        and record.retrieval_valid_until > now
        and record.retrieval_review_due_at is not None
        and record.retrieval_review_due_at > now
        and record.validity.valid_from <= now
        and (record.validity.valid_until is None or record.validity.valid_until > now)
    )


def _base_contributors(run: AnalysisRun) -> list[SocAutomationContributorRef]:
    contributors: list[SocAutomationContributorRef] = []
    if run.analysis is not None:
        contributors.extend(
            SocAutomationContributorRef(
                kind=SocAutomationContributorKind.CURRENT_EVIDENCE,
                role=SocAutomationContributorRole.SUPPORTS,
                ref_id=item.evidence_ref,
            )
            for item in run.analysis.evidence
            if item.evidence_ref
        )
        contributors.extend(
            SocAutomationContributorRef(
                kind=SocAutomationContributorKind.MODEL_REASONING,
                role=SocAutomationContributorRole.SUPPORTS,
                ref_id=item.reasoning_id,
            )
            for item in run.analysis.reasoning
        )
    if run.llm_analysis_request is not None:
        kind_map = {
            AnalysisContextReferenceKind.SKILL: SocAutomationContributorKind.SKILL,
            AnalysisContextReferenceKind.CONFIRMED_MEMORY: SocAutomationContributorKind.CONFIRMED_MEMORY,
            AnalysisContextReferenceKind.GOVERNED_CONTEXT: SocAutomationContributorKind.GOVERNED_CONTEXT,
            AnalysisContextReferenceKind.TOOL_RESULT: SocAutomationContributorKind.TOOL_RESULT,
        }
        for item in run.llm_analysis_request.context_catalog:
            kind = kind_map.get(item.kind)
            if kind is None or kind is SocAutomationContributorKind.CONFIRMED_MEMORY:
                continue
            contributors.append(
                SocAutomationContributorRef(
                    kind=kind,
                    role=SocAutomationContributorRole.OBSERVED,
                    ref_id=item.context_ref,
                    content_hash=item.content_hash,
                )
            )
    return contributors


def _rule_contributor(
    policy: SocAutomationPolicy,
    rule: SocAutomationRule,
) -> SocAutomationContributorRef:
    return SocAutomationContributorRef(
        kind=SocAutomationContributorKind.SYSTEM_POLICY,
        role=SocAutomationContributorRole.AUTHORIZES,
        ref_id=rule.rule_id,
        version=policy.policy_version,
        content_hash=stable_hash(rule.model_dump(mode="json")),
        detail=rule.rationale,
    )


def _dedupe_contributors(
    contributors: list[SocAutomationContributorRef],
) -> list[SocAutomationContributorRef]:
    deduped: dict[tuple[str, str, str], SocAutomationContributorRef] = {}
    for item in contributors:
        key = (item.kind.value, item.role.value, item.ref_id)
        deduped.setdefault(key, item)
    return list(deduped.values())[:300]


def _resolve_action_target(
    run: AnalysisRun,
    selector: SocAutomationTargetSelector,
) -> str | None:
    request = run.llm_analysis_request
    if request is None:
        return None
    if selector is SocAutomationTargetSelector.SOURCE_IP:
        return request.canonical_entities.network.source_ip
    if selector is SocAutomationTargetSelector.DESTINATION_IP:
        return request.canonical_entities.network.destination_ip
    if selector in {
        SocAutomationTargetSelector.ATTACKER_IP,
        SocAutomationTargetSelector.VICTIM_IP,
        SocAutomationTargetSelector.IMPACTED_HOST,
    }:
        role = {
            SocAutomationTargetSelector.ATTACKER_IP: "attacker",
            SocAutomationTargetSelector.VICTIM_IP: "victim",
            SocAutomationTargetSelector.IMPACTED_HOST: "impacted_asset",
        }[selector]
        for resolution in request.fact_reconstruction.role_resolutions:
            if resolution.role == role and resolution.selected_value:
                return resolution.selected_value
        if selector is SocAutomationTargetSelector.IMPACTED_HOST:
            return request.canonical_entities.host.host_name or request.canonical_entities.host.host_id or next(iter(request.canonical_entities.host.ip_addresses), None)
        return None
    user = request.canonical_entities.user
    return user.um_account or user.user_id or user.username


def _find_descriptor(
    registry: SocActionAdapterRegistryPort | None,
    rule: SocAutomationRule,
) -> Any | None:
    if registry is None or rule.action is None:
        return None
    for descriptor in registry.list_descriptors():
        if descriptor.route == rule.action.route and descriptor.action == rule.action.action and descriptor.adapter_id == rule.action.adapter_id:
            return descriptor
    return None


def _authorization_decision(
    policy: SocAutomationPolicy,
    rule: SocAutomationRule,
    *,
    replay_of_run_id: str | None,
    target: str | None,
    descriptor: Any | None,
) -> tuple[SocActionAuthorizationDecision, str, SocAgentRiskLevel]:
    if target is None:
        return (
            SocActionAuthorizationDecision.DENIED,
            "Action target could not be resolved from canonical Runtime facts.",
            SocAgentRiskLevel.UNKNOWN,
        )
    if descriptor is None:
        return (
            SocActionAuthorizationDecision.DENIED,
            "Pinned action adapter is not registered.",
            SocAgentRiskLevel.UNKNOWN,
        )
    if policy.mode is SocAutomationPolicyMode.SHADOW:
        return (
            SocActionAuthorizationDecision.SHADOW_ONLY,
            "Policy matched in shadow mode; no external action is authorized.",
            descriptor.risk_level,
        )
    if rule.authorization_mode is SocActionAuthorizationMode.HUMAN_APPROVAL:
        return (
            SocActionAuthorizationDecision.REQUIRES_HUMAN,
            "Enforced policy requires a human approval grant for this action.",
            descriptor.risk_level,
        )
    if replay_of_run_id is not None:
        return (
            SocActionAuthorizationDecision.DENIED,
            "Automatic external actions are disabled for replay runs.",
            descriptor.risk_level,
        )
    if not descriptor.execute_supported:
        return (
            SocActionAuthorizationDecision.DENIED,
            "Pinned adapter does not support execution.",
            descriptor.risk_level,
        )
    if descriptor.external_side_effect not in {"write", "destructive"}:
        return (
            SocActionAuthorizationDecision.DENIED,
            "Automatic response action must declare a write or destructive side effect.",
            descriptor.risk_level,
        )
    if not descriptor.idempotency_required:
        return (
            SocActionAuthorizationDecision.DENIED,
            "Automatic response adapter must require idempotency.",
            descriptor.risk_level,
        )
    reason = "Reviewed enforced policy authorized the pinned idempotent action adapter."
    if rule.match.needs_review is True:
        reason = f"{reason} The policy explicitly authorized execution while the effective decision still requires review: {rule.review_required_override_reason}"
    return (
        SocActionAuthorizationDecision.AUTHORIZED,
        reason,
        descriptor.risk_level,
    )


def _automation_actor(policy: SocAutomationPolicy) -> ActorContext:
    return ActorContext(
        actor_id=f"soc-automation:{policy.policy_id}",
        actor_type=ActorType.SYSTEM,
        surface=EntrySurface.DAEMON,
        roles=["soc_automation"],
        auth_source=ActorAuthSource.SYSTEM,
    )


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get("payload")
    if isinstance(value, dict):
        candidate = value.get(key)
        return str(candidate) if candidate is not None else None
    return None


def _optional_dict(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = payload.get("payload")
    if isinstance(value, dict) and isinstance(value.get(key), dict):
        return dict(value[key])
    return None


__all__ = [
    "EFFECTIVE_DECISION_POLICY_VERSION",
    "SocAutomationError",
    "SocAutomationService",
]
