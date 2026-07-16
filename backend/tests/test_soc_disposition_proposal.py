from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.authorization import InMemoryAuthorizationEnrichmentRepository
from soc_agent.cli import main
from soc_agent.context_bridge import build_lead_agent_review_context_artifact
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunStatus,
    AuthorizationEnrichmentRecord,
    AuthorizationFactRef,
    AuthorizationMatchResult,
    AuthorizationMatchStatus,
    AuthorizationQuery,
    Decision,
    EntrySurface,
    EvidenceItem,
    GovernedContextFactStatus,
    ReviewQueueItem,
    ReviewQueueStatus,
    ServiceRequestContext,
    SocDispositionProposalCommand,
    SocEvent,
    SocOperationalDisposition,
    Verdict,
)
from soc_agent.core import (
    DispositionProposalIdempotencyConflictError,
    DispositionProposalIneligibleError,
    SocDispositionProposalService,
    SocReviewService,
)
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.disposition import InMemoryDispositionProposalRepository


class InMemoryAlertRepository:
    def __init__(self, *runs: AnalysisRun) -> None:
        self.runs = {run.run_id: run for run in runs}

    def save_run(self, run: AnalysisRun) -> None:
        self.runs[run.run_id] = run

    def get_run(self, run_id: str) -> AnalysisRun | None:
        return self.runs.get(run_id)

    def list_runs(self, *, limit: int = 50) -> list[AnalysisRun]:
        return list(self.runs.values())[:limit]


class CapturingEventSink:
    def __init__(self) -> None:
        self.events: list[SocEvent] = []

    def emit(self, event: SocEvent) -> None:
        self.events.append(event)


class InMemoryReviewQueueRepository:
    def __init__(self, item: ReviewQueueItem | None) -> None:
        self.item = item

    def get_review_item(self, queue_id: str) -> ReviewQueueItem | None:
        if self.item is not None and self.item.queue_id == queue_id:
            return self.item
        return None


def _run(*, verdict: Verdict = Verdict.TRUE_POSITIVE) -> AnalysisRun:
    return AnalysisRun(
        run_id="RUN-DP01",
        alert_id="ALERT-DP01",
        status=AnalysisRunStatus.NEEDS_REVIEW,
        analysis=AnalysisResult(
            verdict=verdict,
            confidence=0.93,
            summary="Observed behavior is real and requires operational context.",
            evidence=[
                EvidenceItem(
                    source="canonical",
                    description="Canonical behavior signature",
                    value="behavior_signature=java->chattr",
                )
            ],
            reason="Canonical evidence supports the detection truth.",
            recommended_action="review authorization context",
        ),
        decision=Decision(
            verdict=verdict,
            confidence=0.93,
            suggested_action="review authorization context",
            needs_review=True,
            reason="Authorization is evaluated outside the detection decision.",
        ),
    )


def _enrichment(
    *,
    enrichment_id: str = "AAE-DP01",
    status: AuthorizationMatchStatus = AuthorizationMatchStatus.EXACT,
    query_hash: str = "a" * 64,
) -> AuthorizationEnrichmentRecord:
    query = AuthorizationQuery(
        query_id=f"AAQ-{enrichment_id}",
        alert_id="ALERT-DP01",
        tenant_id="tenant-a",
        environment="production",
    )
    fact_refs = (
        [
            AuthorizationFactRef(
                fact_id="GCF-AUTHORIZED-JOB",
                fact_version_id="GCFV-AUTHORIZED-JOB-2",
                version=2,
                status=GovernedContextFactStatus.ACTIVE,
                content_hash="b" * 64,
            )
        ]
        if status is AuthorizationMatchStatus.EXACT
        else []
    )
    result = AuthorizationMatchResult(
        query_id=query.query_id,
        alert_id=query.alert_id,
        status=status,
        matched_fact_refs=fact_refs,
        evidence_refs=["fact:GCFV-AUTHORIZED-JOB-2"],
    )
    return AuthorizationEnrichmentRecord(
        enrichment_id=enrichment_id,
        run_id="RUN-DP01",
        alert_id="ALERT-DP01",
        queue_id="REV-DP01",
        query=query,
        query_hash=query_hash,
        match_result=result,
        matcher_policy_version=result.policy_version,
        idempotency_key=f"authorization:{enrichment_id}",
    )


def _service(
    run: AnalysisRun,
    *enrichments: AuthorizationEnrichmentRecord,
    event_sink: CapturingEventSink | None = None,
    review_status: ReviewQueueStatus = ReviewQueueStatus.OPEN,
) -> tuple[SocDispositionProposalService, InMemoryDispositionProposalRepository]:
    proposal_repository = InMemoryDispositionProposalRepository()
    first = enrichments[0]
    review_item = (
        ReviewQueueItem(
            queue_id=first.queue_id,
            run_id=first.run_id,
            alert_id=first.alert_id,
            reason="shadow disposition review",
            status=review_status,
        )
        if first.queue_id is not None
        else None
    )
    return (
        SocDispositionProposalService(
            repository=proposal_repository,
            authorization_enrichment_repository=InMemoryAuthorizationEnrichmentRepository(enrichments),
            alert_repository=InMemoryAlertRepository(run),
            review_queue_repository=InMemoryReviewQueueRepository(review_item),
            event_sink=event_sink,
        ),
        proposal_repository,
    )


def test_exact_authorization_creates_shadow_benign_true_positive_proposal() -> None:
    run = _run()
    enrichment = _enrichment()
    events = CapturingEventSink()
    service, repository = _service(run, enrichment, event_sink=events)
    run_before = run.model_dump(mode="json")

    result = service.propose(
        SocDispositionProposalCommand(
            enrichment_id=enrichment.enrichment_id,
            idempotency_key="disposition:dp01",
        ),
        context=ServiceRequestContext(
            actor=ActorContext(
                actor_id="analyst-1",
                actor_type=ActorType.USER,
                surface=EntrySurface.TEST,
                roles=["soc_analyst"],
            )
        ),
    )

    proposal = result.proposal
    assert proposal.detection_truth.verdict is Verdict.TRUE_POSITIVE
    assert proposal.proposed_disposition is SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE
    assert proposal.source_enrichment_id == enrichment.enrichment_id
    assert proposal.requires_human_review is True
    assert proposal.auto_close_allowed is False
    assert proposal.application_status == "not_applied"
    assert proposal.detection_truth_impact == "none"
    assert proposal.review_queue_impact == "none"
    assert repository.get_disposition_proposal(proposal.proposal_id) == proposal
    assert run.model_dump(mode="json") == run_before
    assert events.events[0].event_type.value == "disposition.proposal_recorded"


def test_proposal_policy_fails_closed_for_non_exact_or_non_true_positive() -> None:
    non_exact = _enrichment(status=AuthorizationMatchStatus.PARTIAL)
    service, _ = _service(_run(), non_exact)
    with pytest.raises(DispositionProposalIneligibleError, match="cannot produce") as non_exact_error:
        service.propose(
            SocDispositionProposalCommand(
                enrichment_id=non_exact.enrichment_id,
                idempotency_key="disposition:partial",
            )
        )
    assert non_exact_error.value.reason_code == "authorization_match_not_exact"

    exact = _enrichment()
    service, _ = _service(_run(verdict=Verdict.FALSE_POSITIVE), exact)
    with pytest.raises(DispositionProposalIneligibleError, match="requires current detection truth") as verdict_error:
        service.propose(
            SocDispositionProposalCommand(
                enrichment_id=exact.enrichment_id,
                idempotency_key="disposition:false-positive",
            )
        )
    assert verdict_error.value.reason_code == "detection_truth_not_true_positive"


def test_proposal_requires_open_review_queue_lineage() -> None:
    unlinked = _enrichment().model_copy(update={"queue_id": None})
    service, _ = _service(_run(), unlinked)
    with pytest.raises(DispositionProposalIneligibleError, match="explicit review queue") as missing_error:
        service.propose(
            SocDispositionProposalCommand(
                enrichment_id=unlinked.enrichment_id,
                idempotency_key="disposition:no-queue",
            )
        )
    assert missing_error.value.reason_code == "review_queue_missing"

    linked = _enrichment()
    service, _ = _service(_run(), linked, review_status=ReviewQueueStatus.CLOSED)
    with pytest.raises(DispositionProposalIneligibleError, match="open review queue") as closed_error:
        service.propose(
            SocDispositionProposalCommand(
                enrichment_id=linked.enrichment_id,
                idempotency_key="disposition:closed-queue",
            )
        )
    assert closed_error.value.reason_code == "review_queue_not_open"


def test_proposal_is_idempotent_and_rejects_retry_key_reuse() -> None:
    first_enrichment = _enrichment()
    second_enrichment = _enrichment(
        enrichment_id="AAE-DP01-SECOND",
        query_hash="c" * 64,
    )
    service, repository = _service(_run(), first_enrichment, second_enrichment)
    command = SocDispositionProposalCommand(
        enrichment_id=first_enrichment.enrichment_id,
        idempotency_key="disposition:stable",
    )

    first = service.propose(command)
    retry = service.propose(command)

    assert retry.idempotent is True
    assert retry.proposal.proposal_id == first.proposal.proposal_id
    assert len(repository.list_disposition_proposals()) == 1
    with pytest.raises(DispositionProposalIdempotencyConflictError, match="different idempotency key"):
        service.propose(
            SocDispositionProposalCommand(
                enrichment_id=first_enrichment.enrichment_id,
                idempotency_key="disposition:alternate-retry-key",
            )
        )
    with pytest.raises(DispositionProposalIdempotencyConflictError):
        service.propose(
            SocDispositionProposalCommand(
                enrichment_id=second_enrichment.enrichment_id,
                idempotency_key="disposition:stable",
            )
        )


def test_sql_repository_projects_proposal_to_review_and_lead_agent_context(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'disposition.db'}"
    engine = create_engine(database_url)
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    run = _run()
    queue = ReviewQueueItem(
        queue_id="REV-DP01",
        run_id=run.run_id,
        alert_id=run.alert_id,
        reason="shadow disposition review",
    )
    enrichment = _enrichment()
    repository.save_run(run)
    repository.save_review_item(queue)
    repository.save_authorization_enrichment(enrichment)
    proposal = (
        SocDispositionProposalService(
            repository=repository,
            authorization_enrichment_repository=repository,
            alert_repository=repository,
            review_queue_repository=repository,
        )
        .propose(
            SocDispositionProposalCommand(
                enrichment_id=enrichment.enrichment_id,
                idempotency_key="disposition:sql",
            )
        )
        .proposal
    )

    context = SocReviewService(
        repository=repository,
        review_queue_repository=repository,
        authorization_enrichment_repository=repository,
        disposition_proposal_repository=repository,
    ).get_investigation_context(queue.queue_id)
    artifact = build_lead_agent_review_context_artifact(context)

    assert repository.get_disposition_proposal(proposal.proposal_id) == proposal
    assert context.queue_item.status.value == "open"
    assert context.disposition_proposals == [proposal]
    assert context.investigation_view is not None
    assert context.investigation_view.counts["disposition_proposals"] == 1
    assert any(item.kind == "disposition_proposal" for item in context.investigation_view.evidence_timeline)
    assert artifact.disposition_proposals[0]["proposal_id"] == proposal.proposal_id
    assert "cannot close a review item" in " ".join(artifact.instructions)
    engine.dispose()


def test_disposition_cli_propose_list_and_get(tmp_path, capsys) -> None:
    database_url = f"sqlite:///{tmp_path / 'disposition-cli.db'}"
    engine = create_engine(database_url)
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    repository.save_run(_run())
    repository.save_review_item(
        ReviewQueueItem(
            queue_id="REV-DP01",
            run_id="RUN-DP01",
            alert_id="ALERT-DP01",
            reason="shadow disposition review",
        )
    )
    repository.save_authorization_enrichment(_enrichment())
    engine.dispose()

    assert main(["disposition", "propose", "AAE-DP01", "--database-url", database_url, "--pretty"]) == 0
    proposal = json.loads(capsys.readouterr().out)["proposal"]
    assert proposal["proposed_disposition"] == "closed_benign_true_positive"
    assert proposal["auto_close_allowed"] is False

    assert main(["disposition", "list", "--run-id", "RUN-DP01", "--database-url", database_url]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [item["proposal_id"] for item in listed] == [proposal["proposal_id"]]

    assert main(["disposition", "get", proposal["proposal_id"], "--database-url", database_url]) == 0
    loaded = json.loads(capsys.readouterr().out)
    assert loaded["source_enrichment_id"] == "AAE-DP01"
