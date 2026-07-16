from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.authorization import InMemoryAuthorizationEnrichmentRepository
from soc_agent.cli import main
from soc_agent.context_bridge import build_lead_agent_review_context_artifact
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AnalysisRun,
    AnalysisRunStatus,
    AuthorizationEnrichmentRecord,
    AuthorizationFactRef,
    AuthorizationMatchResult,
    AuthorizationMatchStatus,
    AuthorizationQuery,
    AuthorizationSourceFreshness,
    EntrySurface,
    GovernedContextFactStatus,
    ReviewQueueItem,
    ReviewQueueStatus,
    ServiceRequestContext,
    SocDetectionTruthSnapshot,
    SocDispositionEvaluationGatePolicy,
    SocDispositionEvaluationGateStatus,
    SocDispositionEvaluationScope,
    SocDispositionOutcomeCommand,
    SocDispositionOutcomeReviewKind,
    SocDispositionOutcomeSource,
    SocDispositionOutcomeStatus,
    SocDispositionProposalReasonCode,
    SocDispositionProposalRecord,
    SocDispositionSampleCreateCommand,
    SocDispositionSampleReviewReadiness,
    SocOperationalDisposition,
    Verdict,
)
from soc_agent.core import (
    DispositionEvaluationIdempotencyConflictError,
    DispositionEvaluationIneligibleError,
    SocDispositionEvaluationService,
    SocReviewService,
)
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.disposition import (
    InMemoryDispositionEvaluationRepository,
    InMemoryDispositionProposalRepository,
)

BASE_TIME = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)


class InMemoryReviewQueueRepository:
    def __init__(self, items: list[ReviewQueueItem]) -> None:
        self.items = {item.queue_id: item for item in items}

    def get_review_item(self, queue_id: str) -> ReviewQueueItem | None:
        return self.items.get(queue_id)


def _actor(actor_id: str) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id=actor_id,
            actor_type=ActorType.USER,
            surface=EntrySurface.CLI,
            roles=["soc_analyst"],
        )
    )


def _enrichment(index: int, *, freshness: bool = True, shared_fact: bool = False) -> AuthorizationEnrichmentRecord:
    alert_id = f"ALERT-EV-{index}"
    query = AuthorizationQuery(
        query_id=f"AAQ-EV-{index}",
        alert_id=alert_id,
        tenant_id="tenant-a",
        environment="production",
        event_time=BASE_TIME,
    )
    fact_suffix = "SHARED" if shared_fact else str(index)
    fact_ref = AuthorizationFactRef(
        fact_id=f"GCF-{fact_suffix}",
        fact_version_id=f"GCFV-{fact_suffix}-1",
        version=1,
        status=GovernedContextFactStatus.ACTIVE,
        content_hash=f"{index + 1:x}" * 64,
    )
    result = AuthorizationMatchResult(
        query_id=query.query_id,
        alert_id=alert_id,
        status=AuthorizationMatchStatus.EXACT,
        matched_fact_refs=[fact_ref],
        source_freshness=[AuthorizationSourceFreshness.FRESH] if freshness else [],
        evidence_refs=[f"fact:{fact_ref.fact_version_id}"],
    )
    return AuthorizationEnrichmentRecord(
        enrichment_id=f"AAE-EV-{index}",
        run_id=f"RUN-EV-{index}",
        alert_id=alert_id,
        queue_id=f"REV-EV-{index}",
        query=query,
        query_hash=f"{index + 1:x}" * 64,
        match_result=result,
        matcher_policy_version=result.policy_version,
        idempotency_key=f"authorization:ev:{index}",
        created_at=BASE_TIME,
    )


def _proposal(index: int, enrichment: AuthorizationEnrichmentRecord) -> SocDispositionProposalRecord:
    return SocDispositionProposalRecord(
        proposal_id=f"DPROP-EV-{index}",
        proposal_key=f"{index + 4:x}" * 64,
        run_id=enrichment.run_id,
        alert_id=enrichment.alert_id,
        queue_id=enrichment.queue_id or "",
        source_enrichment_id=enrichment.enrichment_id,
        source_query_hash=enrichment.query_hash,
        source_matcher_policy_version=enrichment.matcher_policy_version,
        source_fact_refs=enrichment.match_result.matched_fact_refs,
        source_evidence_refs=enrichment.match_result.evidence_refs,
        detection_truth=SocDetectionTruthSnapshot(
            verdict=Verdict.TRUE_POSITIVE,
            confidence=0.95,
            source="decision",
            decision_policy_version="soc.decision_policy.v2",
        ),
        proposed_disposition=SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
        reason_code=SocDispositionProposalReasonCode.AUTHORIZED_ACTIVITY_EXACT_MATCH,
        rationale=["Exact governed authorization matched the true-positive behavior."],
        idempotency_key=f"proposal:ev:{index}",
        created_at=BASE_TIME + timedelta(minutes=index),
    )


def _queue(index: int, *, closed: bool = True) -> ReviewQueueItem:
    return ReviewQueueItem(
        queue_id=f"REV-EV-{index}",
        run_id=f"RUN-EV-{index}",
        alert_id=f"ALERT-EV-{index}",
        reason="evaluate shadow disposition",
        status=ReviewQueueStatus.CLOSED if closed else ReviewQueueStatus.OPEN,
        closed_at=BASE_TIME + timedelta(minutes=10) if closed else None,
        closed_by=_actor("analyst-primary").actor if closed else None,
        close_reason="analyst completed review" if closed else None,
    )


def _scope() -> SocDispositionEvaluationScope:
    return SocDispositionEvaluationScope(
        tenant_id="tenant-a",
        environment="production",
        window_start=BASE_TIME - timedelta(minutes=1),
        window_end=BASE_TIME + timedelta(hours=1),
        proposal_policy_version="soc.disposition_proposal_policy.v1",
        matcher_policy_version="soc.authorization_match.v1",
    )


def _policy() -> SocDispositionEvaluationGatePolicy:
    return SocDispositionEvaluationGatePolicy(
        policy_version="soc.disposition_evaluation.test.v1",
        scope=_scope(),
        accepted_primary_sources=[SocDispositionOutcomeSource.ANALYST],
        accepted_sample_sources=[SocDispositionOutcomeSource.ANALYST],
        minimum_proposal_count=3,
        minimum_resolved_count=3,
        minimum_resolution_rate=1.0,
        minimum_shadow_precision=1.0,
        maximum_override_rate=0.0,
        minimum_sampled_review_count=2,
        minimum_sampled_precision=1.0,
        minimum_sample_coverage_rate=1.0,
        minimum_sample_agreement_count=2,
        minimum_sample_agreement_rate=1.0,
        minimum_freshness_pass_rate=1.0,
        maximum_fact_version_fanout=1,
    )


def _service(
    *,
    freshness: tuple[bool, bool, bool] = (True, True, True),
    shared_fact: bool = False,
    queues_closed: bool = True,
) -> tuple[
    SocDispositionEvaluationService,
    InMemoryDispositionEvaluationRepository,
    list[SocDispositionProposalRecord],
]:
    enrichments = [_enrichment(index, freshness=freshness[index - 1], shared_fact=shared_fact) for index in range(1, 4)]
    proposals = [_proposal(index, enrichments[index - 1]) for index in range(1, 4)]
    evaluation_repository = InMemoryDispositionEvaluationRepository()
    return (
        SocDispositionEvaluationService(
            repository=evaluation_repository,
            proposal_repository=InMemoryDispositionProposalRepository(proposals),
            authorization_enrichment_repository=InMemoryAuthorizationEnrichmentRepository(enrichments),
            review_queue_repository=InMemoryReviewQueueRepository([_queue(index, closed=queues_closed) for index in range(1, 4)]),
        ),
        evaluation_repository,
        proposals,
    )


def test_sample_manifest_is_reproducible_and_idempotent() -> None:
    service, _, _ = _service()
    command = SocDispositionSampleCreateCommand(
        scope=_scope(),
        sample_size=2,
        selection_seed="ev-01-seed",
        idempotency_key="sample:ev:1",
    )

    created = service.create_sample(command, context=_actor("qa-lead"))
    retried = service.create_sample(command, context=_actor("qa-lead"))

    assert created.idempotent is False
    assert retried.idempotent is True
    assert retried.manifest == created.manifest
    assert created.manifest.sample_size == 2
    assert len(set(created.manifest.selected_proposal_ids)) == 2
    with pytest.raises(DispositionEvaluationIdempotencyConflictError):
        service.create_sample(
            command.model_copy(update={"idempotency_key": "sample:ev:other"}),
            context=_actor("qa-lead"),
        )


def test_outcome_requires_closed_queue_and_explicit_supersession() -> None:
    service, repository, proposals = _service()
    command = SocDispositionOutcomeCommand(
        proposal_id=proposals[0].proposal_id,
        observed_disposition=SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
        reason="Analyst confirmed authorized but real activity.",
        idempotency_key="outcome:ev:1",
    )

    created = service.record_outcome(command, context=_actor("analyst-primary"))
    retried = service.record_outcome(command, context=_actor("analyst-primary"))

    assert created.outcome.outcome_status is SocDispositionOutcomeStatus.CONFIRMED
    assert retried.idempotent is True
    with pytest.raises(DispositionEvaluationIneligibleError, match="explicitly supersede"):
        service.record_outcome(
            command.model_copy(
                update={
                    "observed_disposition": SocOperationalDisposition.ESCALATED,
                    "idempotency_key": "outcome:ev:2",
                }
            ),
            context=_actor("analyst-primary"),
        )

    corrected = service.record_outcome(
        command.model_copy(
            update={
                "observed_disposition": SocOperationalDisposition.ESCALATED,
                "reason": "Later evidence requires escalation.",
                "supersedes_outcome_id": created.outcome.outcome_id,
                "idempotency_key": "outcome:ev:3",
                "observed_at": created.outcome.observed_at + timedelta(minutes=1),
            }
        ),
        context=_actor("analyst-primary"),
    )
    assert corrected.outcome.outcome_status is SocDispositionOutcomeStatus.OVERRIDDEN
    assert len(repository.list_disposition_outcomes(proposal_id=proposals[0].proposal_id)) == 2

    open_service, _, _ = _service(queues_closed=False)
    with pytest.raises(DispositionEvaluationIneligibleError, match="closed ReviewQueue"):
        open_service.record_outcome(command, context=_actor("analyst-primary"))


def test_sampled_review_requires_manifest_membership_and_independent_reviewer() -> None:
    service, _, proposals = _service()
    manifest = service.create_sample(
        SocDispositionSampleCreateCommand(
            scope=_scope(),
            sample_size=2,
            selection_seed="sample-review",
            idempotency_key="sample:review",
        ),
        context=_actor("qa-lead"),
    ).manifest
    proposal_id = manifest.selected_proposal_ids[0]
    service.record_outcome(
        SocDispositionOutcomeCommand(
            proposal_id=proposal_id,
            observed_disposition=SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
            reason="Primary review accepted.",
            observed_at=BASE_TIME + timedelta(minutes=20),
            idempotency_key="outcome:primary",
        ),
        context=_actor("analyst-primary"),
    )
    sample_command = SocDispositionOutcomeCommand(
        proposal_id=proposal_id,
        observed_disposition=SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
        review_kind=SocDispositionOutcomeReviewKind.SAMPLED_QUALITY_REVIEW,
        sample_id=manifest.sample_id,
        reason="Independent sample review accepted.",
        observed_at=BASE_TIME + timedelta(minutes=21),
        idempotency_key="outcome:sampled",
    )
    with pytest.raises(DispositionEvaluationIneligibleError, match="independent reviewer"):
        service.record_outcome(sample_command, context=_actor("analyst-primary"))
    sampled = service.record_outcome(sample_command, context=_actor("qa-reviewer"))
    assert sampled.outcome.review_kind is SocDispositionOutcomeReviewKind.SAMPLED_QUALITY_REVIEW

    unsampled_id = next(item.proposal_id for item in proposals if item.proposal_id not in manifest.selected_proposal_ids)
    with pytest.raises(DispositionEvaluationIneligibleError, match="not part"):
        service.record_outcome(
            sample_command.model_copy(
                update={
                    "proposal_id": unsampled_id,
                    "idempotency_key": "outcome:unsampled",
                }
            ),
            context=_actor("qa-reviewer"),
        )


def test_sample_review_inbox_derives_progress_and_reviewer_boundaries() -> None:
    service, _, _ = _service()
    manifest = service.create_sample(
        SocDispositionSampleCreateCommand(
            scope=_scope(),
            sample_size=2,
            selection_seed="sample-inbox",
            idempotency_key="sample:inbox",
        ),
        context=_actor("qa-lead"),
    ).manifest
    conflict_proposal_id, completed_proposal_id = manifest.selected_proposal_ids
    service.record_outcome(
        SocDispositionOutcomeCommand(
            proposal_id=conflict_proposal_id,
            observed_disposition=SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
            reason="Primary analyst resolved the item.",
            observed_at=BASE_TIME + timedelta(minutes=20),
            idempotency_key="outcome:inbox:primary:conflict",
        ),
        context=_actor("qa-reviewer"),
    )
    service.record_outcome(
        SocDispositionOutcomeCommand(
            proposal_id=completed_proposal_id,
            observed_disposition=SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
            reason="Primary analyst resolved the sampled item.",
            observed_at=BASE_TIME + timedelta(minutes=21),
            idempotency_key="outcome:inbox:primary:complete",
        ),
        context=_actor("analyst-other"),
    )
    service.record_outcome(
        SocDispositionOutcomeCommand(
            proposal_id=completed_proposal_id,
            observed_disposition=SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
            review_kind=SocDispositionOutcomeReviewKind.SAMPLED_QUALITY_REVIEW,
            sample_id=manifest.sample_id,
            reason="Independent QA completed the selected sample.",
            observed_at=BASE_TIME + timedelta(minutes=22),
            idempotency_key="outcome:inbox:sample:complete",
        ),
        context=_actor("qa-reviewer"),
    )

    campaigns = service.list_sample_review_campaigns(limit=10)
    inbox = service.get_sample_review_inbox(
        manifest.sample_id,
        reviewer_actor_id="qa-reviewer",
        limit=1,
    )
    second_page = service.get_sample_review_inbox(
        manifest.sample_id,
        reviewer_actor_id="qa-reviewer",
        offset=1,
        limit=1,
    )

    assert campaigns.items == [manifest]
    assert campaigns.has_more is False
    assert inbox.total_count == 2
    assert inbox.completed_count == 1
    assert inbox.remaining_count == 1
    assert inbox.reviewer_conflict_count == 1
    assert inbox.completion_rate == 0.5
    assert inbox.has_more is True
    assert inbox.auto_close_allowed is False
    assert inbox.items[0].proposal_id == conflict_proposal_id
    assert inbox.items[0].readiness is SocDispositionSampleReviewReadiness.READY
    assert inbox.items[0].reviewer_independent is False
    assert inbox.items[0].can_record_outcome is False
    assert "not independent" in inbox.items[0].blocking_reasons[0]
    assert second_page.has_more is False
    assert second_page.items[0].proposal_id == completed_proposal_id
    assert second_page.items[0].readiness is SocDispositionSampleReviewReadiness.COMPLETED
    assert second_page.items[0].sampled_outcome_independent is True
    assert second_page.items[0].can_record_outcome is True


def test_sample_review_inbox_waits_for_primary_queue_closure() -> None:
    service, _, _ = _service(queues_closed=False)
    manifest = service.create_sample(
        SocDispositionSampleCreateCommand(
            scope=_scope(),
            sample_size=1,
            selection_seed="sample-inbox-open",
            idempotency_key="sample:inbox:open",
        ),
        context=_actor("qa-lead"),
    ).manifest

    inbox = service.get_sample_review_inbox(
        manifest.sample_id,
        reviewer_actor_id="qa-reviewer",
    )

    assert inbox.completed_count == 0
    assert inbox.items[0].readiness is SocDispositionSampleReviewReadiness.WAITING_FOR_QUEUE_CLOSE
    assert inbox.items[0].can_record_outcome is False


def test_gate_filters_unapproved_sources_and_late_non_independent_sample() -> None:
    service, _, proposals = _service()
    manifest = service.create_sample(
        SocDispositionSampleCreateCommand(
            scope=_scope(),
            sample_size=1,
            selection_seed="late-primary",
            idempotency_key="sample:late-primary",
        ),
        context=_actor("qa-lead"),
    ).manifest
    sampled_proposal_id = manifest.selected_proposal_ids[0]
    service.record_outcome(
        SocDispositionOutcomeCommand(
            proposal_id=sampled_proposal_id,
            observed_disposition=SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
            review_kind=SocDispositionOutcomeReviewKind.SAMPLED_QUALITY_REVIEW,
            sample_id=manifest.sample_id,
            reason="Sample was recorded before primary resolution.",
            observed_at=BASE_TIME + timedelta(minutes=20),
            idempotency_key="outcome:late:sample",
        ),
        context=_actor("same-reviewer"),
    )
    service.record_outcome(
        SocDispositionOutcomeCommand(
            proposal_id=sampled_proposal_id,
            observed_disposition=SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
            reason="Primary resolution was later recorded by the same reviewer.",
            observed_at=BASE_TIME + timedelta(minutes=21),
            idempotency_key="outcome:late:primary",
        ),
        context=_actor("same-reviewer"),
    )
    replay_proposal = next(item for item in proposals if item.proposal_id != sampled_proposal_id)
    service.record_outcome(
        SocDispositionOutcomeCommand(
            proposal_id=replay_proposal.proposal_id,
            observed_disposition=SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
            source=SocDispositionOutcomeSource.REPLAY_LABEL,
            source_ref="fixture:replay-label",
            reason="Offline replay label is not allowed by this live gate policy.",
            observed_at=BASE_TIME + timedelta(minutes=22),
            idempotency_key="outcome:replay-source",
        ),
        context=_actor("eval-runner"),
    )

    report = service.evaluate(_policy(), proposal_limit=10)

    assert report.resolved_count == 1
    assert report.sampled_review_count == 0
    assert any("not independent" in warning for warning in report.warnings)


def test_gate_report_passes_only_complete_resolved_independently_sampled_cohort() -> None:
    service, _, proposals = _service()
    manifest = service.create_sample(
        SocDispositionSampleCreateCommand(
            scope=_scope(),
            sample_size=2,
            selection_seed="gate-pass",
            idempotency_key="sample:gate-pass",
        ),
        context=_actor("qa-lead"),
    ).manifest
    for index, proposal in enumerate(proposals, start=1):
        service.record_outcome(
            SocDispositionOutcomeCommand(
                proposal_id=proposal.proposal_id,
                observed_disposition=proposal.proposed_disposition,
                reason="Primary analyst accepted shadow proposal.",
                observed_at=BASE_TIME + timedelta(minutes=20 + index),
                idempotency_key=f"outcome:gate:primary:{index}",
            ),
            context=_actor(f"analyst-{index}"),
        )
    for index, proposal_id in enumerate(manifest.selected_proposal_ids, start=1):
        service.record_outcome(
            SocDispositionOutcomeCommand(
                proposal_id=proposal_id,
                observed_disposition=SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
                review_kind=SocDispositionOutcomeReviewKind.SAMPLED_QUALITY_REVIEW,
                sample_id=manifest.sample_id,
                reason="Independent QA accepted shadow proposal.",
                observed_at=BASE_TIME + timedelta(minutes=30 + index),
                idempotency_key=f"outcome:gate:sample:{index}",
            ),
            context=_actor(f"qa-{index}"),
        )

    report = service.evaluate(_policy(), proposal_limit=10)

    assert report.gate_status is SocDispositionEvaluationGateStatus.PASSED_SHADOW_EVALUATION
    assert report.rollout_review_eligible is True
    assert report.auto_close_allowed is False
    assert report.shadow_precision == 1.0
    assert report.sampled_precision == 1.0
    assert report.sample_agreement_rate == 1.0
    assert report.freshness_pass_rate == 1.0
    assert report.maximum_fact_version_fanout == 1


def test_gate_report_holds_shadow_on_override_stale_coverage_and_fanout() -> None:
    service, _, proposals = _service(freshness=(True, False, True), shared_fact=True)
    for index, proposal in enumerate(proposals, start=1):
        observed = SocOperationalDisposition.ESCALATED if index == 1 else SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE
        service.record_outcome(
            SocDispositionOutcomeCommand(
                proposal_id=proposal.proposal_id,
                observed_disposition=observed,
                reason="Evaluation label.",
                observed_at=BASE_TIME + timedelta(minutes=20 + index),
                idempotency_key=f"outcome:fail:{index}",
            ),
            context=_actor(f"analyst-{index}"),
        )

    report = service.evaluate(_policy(), proposal_limit=10)

    assert report.gate_status is SocDispositionEvaluationGateStatus.INSUFFICIENT_DATA
    assert report.rollout_review_eligible is False
    assert report.auto_close_allowed is False
    assert "shadow_precision" in report.rollback_signals
    assert "freshness_pass_rate" in report.rollback_signals
    assert "maximum_fact_version_fanout" in report.rollback_signals


def test_sql_repository_round_trips_sample_and_outcome(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'ev-01.db'}")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    enrichment = _enrichment(1)
    proposal = _proposal(1, enrichment)
    repository.save_run(
        AnalysisRun(
            run_id=proposal.run_id,
            alert_id=proposal.alert_id,
            status=AnalysisRunStatus.NEEDS_REVIEW,
            started_at=BASE_TIME,
        )
    )
    repository.save_authorization_enrichment(enrichment)
    repository.save_disposition_proposal(proposal)
    repository.save_review_item(_queue(1))
    service = SocDispositionEvaluationService(
        repository=repository,
        proposal_repository=repository,
        authorization_enrichment_repository=repository,
        review_queue_repository=repository,
    )
    manifest = service.create_sample(
        SocDispositionSampleCreateCommand(
            scope=_scope(),
            sample_size=1,
            selection_seed="sql",
            idempotency_key="sample:sql",
        ),
        context=_actor("qa-lead"),
    ).manifest
    outcome = service.record_outcome(
        SocDispositionOutcomeCommand(
            proposal_id=proposal.proposal_id,
            observed_disposition=proposal.proposed_disposition,
            reason="SQL round-trip accepted.",
            observed_at=BASE_TIME + timedelta(minutes=20),
            idempotency_key="outcome:sql",
        ),
        context=_actor("analyst-sql"),
    ).outcome

    assert repository.get_disposition_sample_manifest(manifest.sample_id) == manifest
    assert repository.get_disposition_outcome(outcome.outcome_id) == outcome
    assert repository.list_disposition_outcomes(proposal_id=proposal.proposal_id) == [outcome]
    assert repository.list_latest_disposition_outcomes_for_proposals(
        proposal_ids=[proposal.proposal_id],
        review_kind=SocDispositionOutcomeReviewKind.ANALYST_RESOLUTION,
    ) == [outcome]
    context = SocReviewService(
        repository=repository,
        review_queue_repository=repository,
        authorization_enrichment_repository=repository,
        disposition_proposal_repository=repository,
        disposition_evaluation_repository=repository,
    ).get_investigation_context(proposal.queue_id)
    artifact = build_lead_agent_review_context_artifact(context)
    assert context.disposition_outcomes == [outcome]
    assert context.investigation_view is not None
    assert context.investigation_view.counts["disposition_outcomes"] == 1
    assert any(item.kind == "disposition_outcome" for item in context.investigation_view.evidence_timeline)
    assert artifact.disposition_outcomes[0]["outcome_id"] == outcome.outcome_id
    engine.dispose()


def test_disposition_evaluation_cli_creates_sample_records_outcome_and_reports(tmp_path, capsys) -> None:
    database_url = f"sqlite:///{tmp_path / 'ev-01-cli.db'}"
    engine = create_engine(database_url)
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    enrichment = _enrichment(1)
    proposal = _proposal(1, enrichment)
    repository.save_authorization_enrichment(enrichment)
    repository.save_disposition_proposal(proposal)
    repository.save_review_item(_queue(1))
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(_scope().model_dump_json(), encoding="utf-8")
    policy_data = _policy().model_copy(
        update={
            "minimum_proposal_count": 1,
            "minimum_resolved_count": 1,
            "minimum_sampled_review_count": 1,
            "minimum_sample_agreement_count": 1,
        }
    )
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(policy_data.model_dump_json(), encoding="utf-8")

    assert (
        main(
            [
                "disposition",
                "sample",
                "create",
                str(scope_path),
                "--sample-size",
                "1",
                "--seed",
                "cli-seed",
                "--idempotency-key",
                "sample:cli",
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    sample_payload = json.loads(capsys.readouterr().out)
    assert sample_payload["manifest"]["selected_proposal_ids"] == [proposal.proposal_id]

    assert (
        main(
            [
                "disposition",
                "outcome",
                "record",
                proposal.proposal_id,
                "--observed-disposition",
                proposal.proposed_disposition.value,
                "--reason",
                "CLI analyst confirmed the proposal.",
                "--observed-at",
                (BASE_TIME + timedelta(minutes=20)).isoformat(),
                "--idempotency-key",
                "outcome:cli",
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    outcome_payload = json.loads(capsys.readouterr().out)
    assert outcome_payload["outcome"]["outcome_status"] == "confirmed"

    assert (
        main(
            [
                "disposition",
                "evaluate",
                str(policy_path),
                "--proposal-limit",
                "10",
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    report_payload = json.loads(capsys.readouterr().out)
    assert report_payload["proposal_count"] == 1
    assert report_payload["confirmed_count"] == 1
    assert report_payload["auto_close_allowed"] is False
    engine.dispose()
