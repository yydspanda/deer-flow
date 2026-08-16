from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.actions.adapters import SocActionAdapterRegistry
from soc_agent.automation import InMemorySocAutomationRepository
from soc_agent.cli import main
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AdjudicatedRole,
    AdjudicatedRoleStatus,
    AdjudicatedRoleType,
    AnalysisContextCatalogItem,
    AnalysisContextReferenceKind,
    AnalysisReasoningBasis,
    AnalysisReasoningItem,
    DecisionEvidenceState,
    EntrySurface,
    EvidenceItem,
    RoleAdjudicationResult,
    RoleAdjudicationStatus,
    ServiceRequestContext,
    SocActionAuthorizationDecision,
    SocActionAuthorizationMode,
    SocActionExecutionStatus,
    SocAgentActionAdapterDescriptor,
    SocAgentActionCommand,
    SocAgentActionResult,
    SocAgentRiskLevel,
    SocAutomationActionSpec,
    SocAutomationPolicy,
    SocAutomationPolicyMode,
    SocAutomationRule,
    SocAutomationRuleMatch,
    SocAutomationTargetSelector,
    SocDecisionTransitionKind,
    SocDispositionTransitionKind,
    SocMemoryApplicabilitySpec,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionDirective,
    SocMemoryDecisionEffect,
    SocMemoryDecisionImpact,
    SocMemoryRecord,
    SocMemoryReviewEffect,
    SocMemoryTargetArtifact,
    SocOperationalDisposition,
    Verdict,
)
from soc_agent.core import SocAutomationService
from soc_agent.core.runtime import analyze_alert
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.memory import InMemoryMemoryCandidateRepository
from soc_agent.pipeline.evidence_grounding import ground_analysis_evidence
from soc_agent.pipeline.materiality import assess_analysis_materiality

NOW = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)


class _ExecutableBlockAdapter:
    descriptor = SocAgentActionAdapterDescriptor(
        adapter_id="test-block-ip",
        route="response.block_ip",
        action="response.block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        adapter_kind="http",
        external_side_effect="write",
        dry_run_supported=True,
        execute_supported=True,
        idempotency_required=True,
        required_payload_fields=["ip", "duration_seconds"],
        required_context_refs=["run_id", "alert_id"],
    )

    def __init__(self) -> None:
        self.execute_count = 0

    def dry_run(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        return SocAgentActionResult(
            route=command.route,
            action=command.action,
            status="success",
            message="validated",
        )

    def execute(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        self.execute_count += 1
        return SocAgentActionResult(
            route=command.route,
            action=command.action,
            status="success",
            message="blocked",
            payload={
                "external_request_id": "REQ-EXT-1",
                "external_state_before": {"blocked": False},
                "external_state_after": {"blocked": True},
            },
        )


class _RetryOnceBlockAdapter(_ExecutableBlockAdapter):
    def execute(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        self.execute_count += 1
        if self.execute_count == 1:
            raise RuntimeError("temporary provider outage")
        return SocAgentActionResult(
            route=command.route,
            action=command.action,
            status="success",
            message="blocked after retry",
            payload={"external_request_id": "REQ-EXT-RETRY"},
        )


def test_enforced_policy_can_authorize_without_memory() -> None:
    run = _automation_ready_run()
    run.decision = run.decision.model_copy(update={"needs_review": True})
    adapter = _ExecutableBlockAdapter()
    policy = _policy(
        rules=[
            SocAutomationRule(
                rule_id="block-high-risk-source",
                name="Block a policy-approved source",
                match=SocAutomationRuleMatch(
                    verdicts=[run.decision.verdict],
                    evidence_states=[run.decision.evidence_state],
                    model_names=[run.model_name],
                    prompt_versions=[run.prompt_version],
                    decision_policy_versions=[run.decision.policy_version],
                    minimum_confidence=run.decision.confidence,
                    needs_review=run.decision.needs_review,
                ),
                disposition=SocOperationalDisposition.SUPPRESSED,
                action=SocAutomationActionSpec(
                    route="response.block_ip",
                    action="response.block_ip",
                    adapter_id=adapter.descriptor.adapter_id,
                    target_selector=SocAutomationTargetSelector.SOURCE_IP,
                    target_payload_field="ip",
                    static_payload={"duration_seconds": 3600},
                ),
                authorization_mode=SocActionAuthorizationMode.AUTOMATIC_POLICY,
                review_required_override_reason=("Tenant owners explicitly approved this exact high-confidence response rule for unattended operation."),
                rationale="A reviewed tenant policy explicitly permits this action.",
            )
        ]
    )
    repository = InMemorySocAutomationRepository()
    service = SocAutomationService(
        repository=repository,
        policy=policy,
        environment="dev",
        action_adapter_registry=SocActionAdapterRegistry([adapter]),
        execute_authorized_actions=True,
        now_provider=lambda: NOW,
    )

    result = service.evaluate(run, context=_context())

    assert result.decision_transition.transition_kind is SocDecisionTransitionKind.UNCHANGED
    assert not any(contributor.kind.value == "confirmed_memory" for contributor in result.decision_transition.contributors)
    assert result.disposition_transition is not None
    assert result.disposition_transition.transition_kind is SocDispositionTransitionKind.APPLIED
    assert result.authorization is not None
    assert result.authorization.decision is SocActionAuthorizationDecision.AUTHORIZED
    assert "still requires review" in result.authorization.reason
    assert result.authorization.target_value == "203.0.113.10"
    assert result.execution is not None
    assert result.execution.status.value == "succeeded"
    assert result.execution.external_state_before == {"blocked": False}
    assert result.execution.external_state_after == {"blocked": True}
    assert adapter.execute_count == 1

    replay = service.evaluate(run, context=_context())
    assert replay.idempotent is True
    assert replay.execution == result.execution
    assert adapter.execute_count == 1


def test_semantic_action_target_uses_accepted_role_and_materiality_guard() -> None:
    run = _automation_ready_run()
    assert run.analysis is not None
    assert run.llm_analysis_request is not None
    source_fact = next(item for item in run.llm_analysis_request.evidence_catalog if item.source_path == "canonical_entities.network.source_ip")
    role_reasoning = AnalysisReasoningItem(
        reasoning_id="R-02",
        statement="The accepted role assessment maps this current IP to attacker.",
        basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE],
        evidence_refs=[source_fact.evidence_ref],
        confidence=0.95,
    )
    run.analysis = run.analysis.model_copy(
        update={
            "evidence": [
                *run.analysis.evidence,
                EvidenceItem(
                    evidence_ref=source_fact.evidence_ref,
                    source=source_fact.source_path,
                    description="Runtime-hydrated current-alert catalog fact",
                    value=source_fact.value,
                ),
            ],
            "reasoning": [*run.analysis.reasoning, role_reasoning],
            "role_adjudication": RoleAdjudicationResult(
                status=RoleAdjudicationStatus.RESOLVED_FROM_EVIDENCE,
                roles=[
                    AdjudicatedRole(
                        role=AdjudicatedRoleType.ATTACKER,
                        entity_type="ip",
                        value="203.0.113.10",
                        status=AdjudicatedRoleStatus.RESOLVED_FROM_EVIDENCE,
                        confidence=0.95,
                        evidence_refs=[source_fact.evidence_ref],
                        reasoning_refs=["R-02"],
                        rationale="Current evidence supports the attacker role.",
                    )
                ],
                rationale="The attacker role is available for governed targeting.",
            ),
        }
    )
    run.analysis_evidence_grounding = ground_analysis_evidence(
        run.analysis,
        run.llm_analysis_request,
    )
    run.analysis_materiality = assess_analysis_materiality(
        run.analysis,
        request=run.llm_analysis_request,
        grounding=run.analysis_evidence_grounding,
        output_quality=run.analysis_output_quality,
    )
    adapter = _ExecutableBlockAdapter()
    policy = _policy(
        rules=[
            SocAutomationRule(
                rule_id="block-resolved-attacker",
                name="Block the accepted attacker role",
                match=SocAutomationRuleMatch(
                    verdicts=[run.decision.verdict],
                    evidence_states=[run.decision.evidence_state],
                    model_names=[run.model_name],
                    prompt_versions=[run.prompt_version],
                    decision_policy_versions=[run.decision.policy_version],
                    minimum_confidence=run.decision.confidence,
                    needs_review=False,
                ),
                action=SocAutomationActionSpec(
                    route="response.block_ip",
                    action="response.block_ip",
                    adapter_id=adapter.descriptor.adapter_id,
                    target_selector=SocAutomationTargetSelector.ATTACKER_IP,
                    target_payload_field="ip",
                    static_payload={"duration_seconds": 3600},
                ),
                authorization_mode=SocActionAuthorizationMode.AUTOMATIC_POLICY,
                rationale="Reviewed policy permits this exact resolved role target.",
            )
        ]
    )

    result = SocAutomationService(
        repository=InMemorySocAutomationRepository(),
        policy=policy,
        environment="dev",
        action_adapter_registry=SocActionAdapterRegistry([adapter]),
        execute_authorized_actions=True,
        now_provider=lambda: NOW,
    ).evaluate(run, context=_context())

    assert result.authorization is not None
    assert result.authorization.decision is SocActionAuthorizationDecision.AUTHORIZED
    assert result.authorization.target_value == "203.0.113.10"
    assert result.execution is not None
    assert result.execution.status is SocActionExecutionStatus.SUCCEEDED


def test_automatic_policy_rejects_implicit_review_guard_bypass() -> None:
    run = _automation_ready_run()
    run.decision = run.decision.model_copy(update={"needs_review": True})

    with pytest.raises(
        ValueError,
        match="review_required_override_reason",
    ):
        SocAutomationRule(
            rule_id="implicit-review-bypass",
            name="Implicit review bypass",
            match=SocAutomationRuleMatch(
                verdicts=[run.decision.verdict],
                evidence_states=[run.decision.evidence_state],
                model_names=[run.model_name],
                prompt_versions=[run.prompt_version],
                decision_policy_versions=[run.decision.policy_version],
                minimum_confidence=run.decision.confidence,
                needs_review=True,
            ),
            action=SocAutomationActionSpec(
                route="response.block_ip",
                action="response.block_ip",
                adapter_id="test-block-ip",
                target_selector=SocAutomationTargetSelector.SOURCE_IP,
                target_payload_field="ip",
            ),
            authorization_mode=SocActionAuthorizationMode.AUTOMATIC_POLICY,
            rationale="This must not validate without an explicit override reason.",
        )


def test_automatic_policy_requires_pinned_analysis_provenance() -> None:
    run = _automation_ready_run()

    with pytest.raises(ValueError, match="model-name"):
        SocAutomationRule(
            rule_id="unpinned-analysis-provenance",
            name="Unpinned analysis provenance",
            match=SocAutomationRuleMatch(
                verdicts=[run.decision.verdict],
                evidence_states=[run.decision.evidence_state],
                minimum_confidence=run.decision.confidence,
                needs_review=False,
            ),
            action=SocAutomationActionSpec(
                route="response.block_ip",
                action="response.block_ip",
                adapter_id="test-block-ip",
                target_selector=SocAutomationTargetSelector.SOURCE_IP,
                target_payload_field="ip",
            ),
            authorization_mode=SocActionAuthorizationMode.AUTOMATIC_POLICY,
            rationale="Automatic execution must not survive an unreviewed model change.",
        )


def test_automatic_policy_never_executes_on_replay_run() -> None:
    run = _automation_ready_run()
    run.replay_of_run_id = "RUN-ORIGINAL"
    adapter = _ExecutableBlockAdapter()
    policy = _policy(
        rules=[
            SocAutomationRule(
                rule_id="block-live-source-only",
                name="Block only during live analysis",
                match=SocAutomationRuleMatch(
                    verdicts=[run.decision.verdict],
                    evidence_states=[run.decision.evidence_state],
                    model_names=[run.model_name],
                    prompt_versions=[run.prompt_version],
                    decision_policy_versions=[run.decision.policy_version],
                    minimum_confidence=run.decision.confidence,
                    needs_review=False,
                ),
                action=SocAutomationActionSpec(
                    route="response.block_ip",
                    action="response.block_ip",
                    adapter_id=adapter.descriptor.adapter_id,
                    target_selector=SocAutomationTargetSelector.SOURCE_IP,
                    target_payload_field="ip",
                    static_payload={"duration_seconds": 3600},
                ),
                authorization_mode=SocActionAuthorizationMode.AUTOMATIC_POLICY,
                rationale="The reviewed rule applies only to first-pass live analysis.",
            )
        ]
    )

    result = SocAutomationService(
        repository=InMemorySocAutomationRepository(),
        policy=policy,
        environment="dev",
        action_adapter_registry=SocActionAdapterRegistry([adapter]),
        execute_authorized_actions=True,
        now_provider=lambda: NOW,
    ).evaluate(run, context=_context())

    assert result.authorization is not None
    assert result.authorization.decision is SocActionAuthorizationDecision.DENIED
    assert "replay" in result.authorization.reason.casefold()
    assert result.execution is None
    assert adapter.execute_count == 0


def test_retryable_execution_reuses_authorization_and_idempotency_key() -> None:
    run = _automation_ready_run()
    adapter = _RetryOnceBlockAdapter()
    policy = _policy(
        rules=[
            SocAutomationRule(
                rule_id="retry-block-source",
                name="Retry a policy-approved block",
                match=SocAutomationRuleMatch(
                    verdicts=[run.decision.verdict],
                    evidence_states=[run.decision.evidence_state],
                    model_names=[run.model_name],
                    prompt_versions=[run.prompt_version],
                    decision_policy_versions=[run.decision.policy_version],
                    minimum_confidence=run.decision.confidence,
                    needs_review=False,
                ),
                action=SocAutomationActionSpec(
                    route="response.block_ip",
                    action="response.block_ip",
                    adapter_id=adapter.descriptor.adapter_id,
                    target_selector=SocAutomationTargetSelector.SOURCE_IP,
                    target_payload_field="ip",
                    static_payload={"duration_seconds": 3600},
                ),
                authorization_mode=SocActionAuthorizationMode.AUTOMATIC_POLICY,
                rationale="Reviewed response policy permits bounded retries.",
            )
        ]
    )
    repository = InMemorySocAutomationRepository()
    service = SocAutomationService(
        repository=repository,
        policy=policy,
        environment="dev",
        action_adapter_registry=SocActionAdapterRegistry([adapter]),
        execute_authorized_actions=True,
        now_provider=lambda: NOW,
    )

    first = service.evaluate(run, context=_context())
    second = service.evaluate(run, context=_context())

    assert first.execution is not None
    assert first.execution.status.value == "failed_retryable"
    assert first.execution.attempt == 1
    assert second.execution is not None
    assert second.execution.status.value == "succeeded"
    assert second.execution.attempt == 2
    assert second.execution.idempotency_key == first.execution.idempotency_key
    assert adapter.execute_count == 2


def test_sql_repository_persists_complete_automation_lineage() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    run = _automation_ready_run()
    adapter = _ExecutableBlockAdapter()
    policy = _policy(
        rules=[
            SocAutomationRule(
                rule_id="persist-block-source",
                name="Persist an authorized block",
                match=SocAutomationRuleMatch(
                    verdicts=[run.decision.verdict],
                    evidence_states=[run.decision.evidence_state],
                    model_names=[run.model_name],
                    prompt_versions=[run.prompt_version],
                    decision_policy_versions=[run.decision.policy_version],
                    minimum_confidence=run.decision.confidence,
                    needs_review=False,
                ),
                disposition=SocOperationalDisposition.SUPPRESSED,
                action=SocAutomationActionSpec(
                    route="response.block_ip",
                    action="response.block_ip",
                    adapter_id=adapter.descriptor.adapter_id,
                    target_selector=SocAutomationTargetSelector.SOURCE_IP,
                    target_payload_field="ip",
                    static_payload={"duration_seconds": 3600},
                ),
                authorization_mode=SocActionAuthorizationMode.AUTOMATIC_POLICY,
                rationale="Persist the full governed lineage.",
            )
        ]
    )
    service = SocAutomationService(
        repository=repository,
        policy=policy,
        environment="dev",
        action_adapter_registry=SocActionAdapterRegistry([adapter]),
        execute_authorized_actions=True,
        now_provider=lambda: NOW,
    )

    result = service.evaluate(run, context=_context())

    assert repository.list_decision_transitions(run_id=run.run_id) == [result.decision_transition]
    assert repository.list_disposition_transitions(run_id=run.run_id) == [result.disposition_transition]
    assert repository.list_action_authorizations(run_id=run.run_id) == [result.authorization]
    assert repository.list_action_executions(run_id=run.run_id) == [result.execution]


def test_automation_lineage_cli_exposes_before_after_and_execution(
    tmp_path,
    capsys,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'automation-lineage.db'}"
    engine = create_engine(database_url)
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    run = _automation_ready_run()
    SocAutomationService(
        repository=repository,
        policy=_policy(rules=[]),
        environment="dev",
        now_provider=lambda: NOW,
    ).evaluate(run, context=_context())
    engine.dispose()

    exit_code = main(
        [
            "automation",
            "lineage",
            "--run-id",
            run.run_id,
            "--database-url",
            database_url,
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["decision_transitions"][0]["before"]["verdict"] == "true_positive"
    assert payload["decision_transitions"][0]["after"]["verdict"] == "true_positive"
    assert payload["action_authorizations"] == []
    assert payload["action_executions"] == []


def test_explicit_confirmed_memory_directive_changes_effective_decision() -> None:
    run = _runtime_run()
    memory_repository = InMemoryMemoryCandidateRepository()
    record = _active_memory_record()
    memory_repository.save_memory_record(record)
    memory_ref = "M-123456789ABC"
    run.llm_analysis_request = run.llm_analysis_request.model_copy(update={"context_catalog": [_memory_catalog_item(record, memory_ref)]})
    service = SocAutomationService(
        repository=InMemorySocAutomationRepository(),
        policy=_policy(rules=[]),
        environment="dev",
        memory_repository=memory_repository,
        now_provider=lambda: NOW,
    )

    result = service.evaluate(run, context=_context())

    transition = result.decision_transition
    assert transition.before.verdict is Verdict.UNKNOWN
    assert transition.after.verdict is Verdict.TRUE_POSITIVE
    assert transition.after.needs_review is False
    assert transition.transition_kind is SocDecisionTransitionKind.OVERRIDDEN
    memory_contributors = [item for item in transition.contributors if item.kind.value == "confirmed_memory"]
    assert [item.ref_id for item in memory_contributors] == [memory_ref]
    assert transition.counterfactual_status == "not_measured"


def test_memory_directive_requires_exact_projected_record_hashes() -> None:
    run = _runtime_run()
    memory_repository = InMemoryMemoryCandidateRepository()
    record = _active_memory_record()
    memory_repository.save_memory_record(record)
    context_item = _memory_catalog_item(record, "M-000000000001")
    context_item = context_item.model_copy(
        update={
            "metadata": {
                **context_item.metadata,
                "record_content_hash": f"sha256:{'0' * 64}",
            }
        }
    )
    run.llm_analysis_request = run.llm_analysis_request.model_copy(update={"context_catalog": [context_item]})

    result = SocAutomationService(
        repository=InMemorySocAutomationRepository(),
        policy=_policy(rules=[]),
        environment="dev",
        memory_repository=memory_repository,
        now_provider=lambda: NOW,
    ).evaluate(run, context=_context())

    assert result.decision_transition.transition_kind is SocDecisionTransitionKind.UNCHANGED
    assert result.decision_transition.after == result.decision_transition.before


def test_context_only_memory_match_cannot_apply_reviewed_decision_directive() -> None:
    run = _runtime_run()
    memory_repository = InMemoryMemoryCandidateRepository()
    record = _active_memory_record().model_copy(
        update={
            "applicability": SocMemoryApplicabilitySpec(
                profile_id="pingan.soc",
                profile_version="2",
                feature_schema_version="pingan.soc.memory_features.v2",
                required_facets={
                    "detection_key": ["rule-a"],
                    "behavior_fingerprint": ["behavior-a"],
                    "environment": ["prd"],
                },
                optional_facets={"behavior_component": ["process:cmd.exe"]},
                minimum_strong_anchor_matches=2,
                context_only_required_facet_keys=[
                    "detection_key",
                    "environment",
                ],
                context_only_missing_facet_keys=["behavior_fingerprint"],
                context_only_similarity_facet_keys=["behavior_component"],
            )
        }
    )
    memory_repository.save_memory_record(record)
    context_item = _memory_catalog_item(record, "M-ABCDEF123456")
    context_item = context_item.model_copy(
        update={
            "metadata": {
                **context_item.metadata,
                "applicability_status": "partial",
                "context_only": True,
                "decision_directive_applicable": False,
            }
        }
    )
    run.llm_analysis_request = run.llm_analysis_request.model_copy(update={"context_catalog": [context_item]})

    result = SocAutomationService(
        repository=InMemorySocAutomationRepository(),
        policy=_policy(rules=[]),
        environment="dev",
        memory_repository=memory_repository,
        now_provider=lambda: NOW,
    ).evaluate(run, context=_context())

    assert result.decision_transition.transition_kind is SocDecisionTransitionKind.UNCHANGED
    assert result.decision_transition.after == result.decision_transition.before


def test_legacy_memory_without_typed_applicability_cannot_apply_directive() -> None:
    run = _runtime_run()
    memory_repository = InMemoryMemoryCandidateRepository()
    record = _active_memory_record().model_copy(update={"applicability": None})
    memory_repository.save_memory_record(record)
    context_item = _memory_catalog_item(record, "M-1E6AC0000001")
    context_item = context_item.model_copy(
        update={
            "metadata": {
                **context_item.metadata,
                # Even stale or forged projection metadata cannot restore
                # deterministic authority to an untyped legacy record.
                "applicability_status": "applicable",
                "decision_directive_applicable": True,
            }
        }
    )
    run.llm_analysis_request = run.llm_analysis_request.model_copy(update={"context_catalog": [context_item]})

    result = SocAutomationService(
        repository=InMemorySocAutomationRepository(),
        policy=_policy(rules=[]),
        environment="dev",
        memory_repository=memory_repository,
        now_provider=lambda: NOW,
    ).evaluate(run, context=_context())

    assert result.decision_transition.transition_kind is SocDecisionTransitionKind.UNCHANGED
    assert result.decision_transition.after == result.decision_transition.before


def test_conflicting_memory_overrides_block_automation_rule_selection() -> None:
    run = _runtime_run()
    memory_repository = InMemoryMemoryCandidateRepository()
    first = _active_memory_record()
    second = first.model_copy(
        update={
            "memory_id": "MEM-AUTOMATION-2",
            "source_candidate_id": "MC-AUTOMATION-2",
            "decision_directive": first.decision_directive.model_copy(update={"target_verdict": Verdict.FALSE_POSITIVE}),
            "content_hash": f"sha256:{'b' * 64}",
        }
    )
    memory_repository.save_memory_record(first)
    memory_repository.save_memory_record(second)
    run.llm_analysis_request = run.llm_analysis_request.model_copy(
        update={
            "context_catalog": [
                _memory_catalog_item(first, "M-111111111111"),
                _memory_catalog_item(second, "M-222222222222"),
            ]
        }
    )
    policy = _policy(
        rules=[
            SocAutomationRule(
                rule_id="must-not-run-on-memory-conflict",
                name="Blocked by memory conflict",
                match=SocAutomationRuleMatch(
                    verdicts=[run.decision.verdict],
                    evidence_states=[run.decision.evidence_state],
                    model_names=[run.model_name],
                    prompt_versions=[run.prompt_version],
                    decision_policy_versions=[run.decision.policy_version],
                    minimum_confidence=run.decision.confidence,
                    needs_review=True,
                ),
                disposition=SocOperationalDisposition.SUPPRESSED,
                action=SocAutomationActionSpec(
                    route="response.block_ip",
                    action="response.block_ip",
                    adapter_id="test-block-ip",
                    target_selector=SocAutomationTargetSelector.SOURCE_IP,
                    target_payload_field="ip",
                ),
                authorization_mode=SocActionAuthorizationMode.AUTOMATIC_POLICY,
                review_required_override_reason="This must never bypass a Memory conflict.",
                rationale="Conflict guard regression rule.",
            )
        ]
    )

    result = SocAutomationService(
        repository=InMemorySocAutomationRepository(),
        policy=policy,
        environment="dev",
        memory_repository=memory_repository,
        action_adapter_registry=SocActionAdapterRegistry([_ExecutableBlockAdapter()]),
        execute_authorized_actions=True,
        now_provider=lambda: NOW,
    ).evaluate(run, context=_context())

    assert result.decision_transition.transition_kind is SocDecisionTransitionKind.CONFLICTED
    assert result.decision_transition.after.needs_review is True
    assert result.selected_rule_id is None
    assert result.disposition_transition is None
    assert result.authorization is None
    assert result.execution is None


def _runtime_run():
    run = analyze_alert(
        {
            "schema_version": "soc.alert.v1",
            "tenant_id": "tenant-a",
            "alert_id": "ALERT-AUTO-1",
            "source": {"source_type": "nids", "source_system": "test"},
            "detection": {"detection_key": "DET-1", "rule_name": "Test"},
            "classification": {"severity": "high", "category": "network"},
            "entities": {
                "network": {
                    "source_ip": "203.0.113.10",
                    "destination_ip": "10.0.0.8",
                }
            },
            "evidence": [],
            "raw": {},
        }
    )
    assert run.decision is not None
    assert run.llm_analysis_request is not None
    return run


def _automation_ready_run():
    run = _runtime_run()
    assert run.decision is not None
    run.decision = run.decision.model_copy(
        update={
            "verdict": Verdict.TRUE_POSITIVE,
            "confidence": 0.95,
            "evidence_state": DecisionEvidenceState.SUFFICIENT,
            "needs_review": False,
            "suggested_action": "block the confirmed attack source",
        }
    )
    return run


def _policy(*, rules: list[SocAutomationRule]) -> SocAutomationPolicy:
    return SocAutomationPolicy(
        policy_id="tenant-a-response-policy",
        policy_version="2026-08-11.1",
        tenant_id="tenant-a",
        environment="dev",
        mode=SocAutomationPolicyMode.ENFORCED,
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=30),
        reviewed_by="soc-owner",
        reviewed_at=NOW - timedelta(hours=1),
        rules=rules,
    )


def _active_memory_record() -> SocMemoryRecord:
    content = "This reviewed NIDS pattern is a confirmed true positive."
    return SocMemoryRecord(
        memory_id="MEM-AUTOMATION-1",
        version=3,
        memory_type=SocMemoryCandidateType.DETECTION_LESSON,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        tenant_scope="tenant-a",
        tenant_id="tenant-a",
        source_candidate_id="MC-AUTOMATION-1",
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.MANUAL_NOTE,
            source_id="review-1",
        ),
        summary="Confirmed NIDS true-positive pattern",
        content=content,
        facets={
            "source_type": ["nids"],
            "detection_key": ["rule-automation-a"],
            "behavior_fingerprint": ["behavior-automation-a"],
            "environment": ["dev"],
        },
        applicability=SocMemoryApplicabilitySpec(
            profile_id="test.soc",
            profile_version="2",
            feature_schema_version="test.soc.memory_features.v2",
            required_facets={
                "detection_key": ["rule-automation-a"],
                "behavior_fingerprint": ["behavior-automation-a"],
                "environment": ["dev"],
            },
            optional_facets={"source_type": ["nids"]},
            minimum_strong_anchor_matches=2,
        ),
        evidence_refs=["review:1"],
        validity=SocMemoryCandidateValidity(
            valid_from=NOW - timedelta(days=1),
            valid_until=NOW + timedelta(days=60),
            notes="Reviewed operational lesson.",
        ),
        confidence=0.95,
        decision_impact=SocMemoryDecisionImpact.DETECTION_DECISION,
        decision_directive=SocMemoryDecisionDirective(
            effect=SocMemoryDecisionEffect.OVERRIDE,
            target_verdict=Verdict.TRUE_POSITIVE,
            review_effect=SocMemoryReviewEffect.CLEAR,
            suggested_action="apply tenant response policy",
            minimum_match_score=5.0,
            required_facet_keys=[
                "detection_key",
                "behavior_fingerprint",
                "environment",
            ],
            rationale="An analyst explicitly approved this scoped override.",
        ),
        content_hash=f"sha256:{hashlib.sha256(content.encode()).hexdigest()}",
        facets_hash=f"sha256:{hashlib.sha256(b'source_type=nids').hexdigest()}",
        retrieval_enabled=True,
        retrieval_policy_version="soc.memory_retrieval_activation_policy.v1",
        retrieval_valid_until=NOW + timedelta(days=30),
        retrieval_review_due_at=NOW + timedelta(days=7),
        retrieval_updated_by=ActorContext(
            actor_id="memory-reviewer",
            actor_type=ActorType.USER,
            surface=EntrySurface.WEB,
        ),
        retrieval_updated_at=NOW - timedelta(hours=1),
        retrieval_reason="Approved for bounded use.",
        created_by=ActorContext(actor_id="memory-reviewer"),
        created_at=NOW - timedelta(days=1),
        updated_at=NOW - timedelta(hours=1),
    )


def _memory_catalog_item(
    record: SocMemoryRecord,
    context_ref: str,
) -> AnalysisContextCatalogItem:
    matched_facets = record.applicability.required_facets if record.applicability is not None else record.facets
    applicability_status = "applicable" if record.applicability is not None else "legacy_anchor_only"
    return AnalysisContextCatalogItem(
        context_ref=context_ref,
        kind=AnalysisContextReferenceKind.CONFIRMED_MEMORY,
        label=record.summary,
        source_id=f"{record.memory_id}@v{record.version}",
        summary=record.content,
        content_hash="a" * 64,
        metadata={
            "memory_id": record.memory_id,
            "memory_version": record.version,
            "retrieval_score": 9.0,
            "matched_facets": matched_facets,
            "applicability_status": applicability_status,
            "decision_directive_applicable": record.applicability is not None,
            "record_content_hash": record.content_hash,
            "record_facets_hash": record.facets_hash,
        },
    )


def _context() -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-daemon",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.DAEMON,
            roles=["soc_daemon"],
        ),
        idempotency_key="automation:test",
    )
