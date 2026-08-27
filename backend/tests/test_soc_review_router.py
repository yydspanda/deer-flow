from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gateway.routers import soc_review
from app.gateway.soc_lead_agent_messages import ResolvedSocLeadAgentMessage
from soc_agent.contracts import (
    AdjudicatedRoleType,
    AlertSummary,
    AnalysisRun,
    DecisionAuditRecord,
    DecisionConfidenceSource,
    EntrySurface,
    HumanConfirmedResponseTarget,
    HumanConfirmedRole,
    InvestigationEvidence,
    ReviewQueueItem,
    ReviewQueueStatus,
    SimilarAlertMatch,
    SimilarAlertQuery,
    SocDispositionOutcomeReviewKind,
    SocDispositionOutcomeSource,
    SocExternalDispositionApplyStatus,
    SocExternalDispositionCanonicalStatus,
    SocExternalDispositionEvent,
    SocExternalDispositionRecord,
    SocLeadAgentReviewContextProvenance,
    SocMemoryCandidate,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateStatus,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionImpact,
    SocMemoryTargetArtifact,
    SocOperationalDisposition,
    Verdict,
)
from soc_agent.core import SocAnalysisService, SocMemoryService, SocReviewService

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


class InMemorySocRepository:
    def __init__(self) -> None:
        self.runs: dict[str, AnalysisRun] = {}
        self.summaries: dict[str, AlertSummary] = {}
        self.review_items: dict[str, ReviewQueueItem] = {}
        self.audit_records: list[DecisionAuditRecord] = []
        self.evidence: list[InvestigationEvidence] = []
        self.external_dispositions: list[SocExternalDispositionRecord] = []
        self.memory_candidates: list[SocMemoryCandidate] = []

    def save_run(self, run: AnalysisRun) -> None:
        self.runs[run.run_id] = run

    def get_run(self, run_id: str) -> AnalysisRun | None:
        return self.runs.get(run_id)

    def list_runs(self, *, limit: int = 50) -> list[AnalysisRun]:
        return list(self.runs.values())[-limit:]

    def save_alert_summary(self, summary: AlertSummary) -> None:
        self.summaries[summary.run_id] = summary

    def get_alert_summary(self, run_id: str) -> AlertSummary | None:
        return self.summaries.get(run_id)

    def list_alert_summaries(self, *, limit: int = 50) -> list[AlertSummary]:
        return list(self.summaries.values())[:limit]

    def find_similar_alert_summaries(self, query: SimilarAlertQuery) -> list[SimilarAlertMatch]:
        return []

    def save_review_item(self, item: ReviewQueueItem) -> None:
        self.review_items[item.queue_id] = item

    def get_review_item(self, queue_id: str) -> ReviewQueueItem | None:
        return self.review_items.get(queue_id)

    def get_open_review_item_by_run(self, run_id: str) -> ReviewQueueItem | None:
        for item in self.review_items.values():
            if item.run_id == run_id and item.status == ReviewQueueStatus.OPEN:
                return item
        return None

    def list_review_items(
        self,
        *,
        status: ReviewQueueStatus | None = None,
        limit: int = 50,
    ) -> list[ReviewQueueItem]:
        items = list(self.review_items.values())
        if status is not None:
            items = [item for item in items if item.status == status]
        return items[:limit]

    def save_audit_record(self, record: DecisionAuditRecord) -> None:
        self.audit_records.append(record)

    def list_audit_records(self, run_id: str) -> list[DecisionAuditRecord]:
        return [record for record in self.audit_records if record.run_id == run_id]

    def save_evidence(self, evidence: InvestigationEvidence) -> None:
        self.evidence.append(evidence)

    def list_evidence(
        self,
        *,
        queue_id: str | None = None,
        run_id: str | None = None,
        alert_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 20,
    ) -> list[InvestigationEvidence]:
        filters = {
            "queue_id": queue_id,
            "run_id": run_id,
            "alert_id": alert_id,
            "thread_id": thread_id,
        }
        active_filters = {key: value for key, value in filters.items() if value}
        evidence = self.evidence
        if active_filters:
            evidence = [item for item in evidence if any(getattr(item, key) == value for key, value in active_filters.items())]
        return sorted(evidence, key=lambda item: item.created_at, reverse=True)[:limit]

    def save_external_disposition(self, record: SocExternalDispositionRecord) -> None:
        self.external_dispositions.append(record)

    def find_external_disposition_by_idempotency_key(self, idempotency_key: str) -> SocExternalDispositionRecord | None:
        for record in self.external_dispositions:
            if record.idempotency_key == idempotency_key:
                return record
        return None

    def list_external_dispositions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        external_system: str | None = None,
        external_case_id: str | None = None,
        limit: int = 50,
    ) -> list[SocExternalDispositionRecord]:
        target_filters = {
            "target_run_id": run_id,
            "target_alert_id": alert_id,
            "target_queue_id": queue_id,
        }
        active_target_filters = {key: value for key, value in target_filters.items() if value}
        records = self.external_dispositions
        if active_target_filters:
            records = [item for item in records if any(getattr(item, key) == value for key, value in active_target_filters.items())]
        if external_system:
            records = [item for item in records if item.event.external_system == external_system]
        if external_case_id:
            records = [item for item in records if item.event.external_case_id == external_case_id]
        return sorted(records, key=lambda item: item.created_at, reverse=True)[:limit]

    def save_memory_candidate(self, candidate: SocMemoryCandidate) -> None:
        self.memory_candidates.append(candidate)

    def get_memory_candidate(self, candidate_id: str) -> SocMemoryCandidate | None:
        for candidate in self.memory_candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        return None

    def find_memory_candidate_by_idempotency_key(self, idempotency_key: str) -> SocMemoryCandidate | None:
        for candidate in self.memory_candidates:
            if candidate.idempotency_key == idempotency_key:
                return candidate
        return None

    def list_memory_candidates(
        self,
        *,
        status: SocMemoryCandidateStatus | None = None,
        tenant_scope: str | None = None,
        tenant_id: str | None = None,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        limit: int = 50,
    ) -> list[SocMemoryCandidate]:
        candidates = self.memory_candidates
        if status is not None:
            candidates = [item for item in candidates if item.status == status]
        if tenant_scope is not None:
            candidates = [item for item in candidates if item.tenant_scope == tenant_scope]
        if tenant_id is not None:
            candidates = [item for item in candidates if item.tenant_id == tenant_id]
        source_filters = {
            "run_id": run_id,
            "alert_id": alert_id,
            "queue_id": queue_id,
        }
        active_source_filters = {key: value for key, value in source_filters.items() if value}
        if active_source_filters:
            candidates = [item for item in candidates if any(getattr(item.source, key) == value for key, value in active_source_filters.items())]
        return sorted(candidates, key=lambda item: item.created_at, reverse=True)[:limit]


def _sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


class FakeRequest:
    def __init__(self, headers: dict[str, str] | None = None, user_id: str | None = None) -> None:
        self.headers = headers or {}
        self.state = SimpleNamespace(auth_source="session")
        if user_id is not None:
            self.state.user = SimpleNamespace(id=user_id, system_role="user")


class BypassAuthFakeRequest(FakeRequest):
    _deerflow_test_bypass_auth = True


@pytest.fixture
def review_api() -> tuple[SocReviewService, InMemorySocRepository, ReviewQueueItem]:
    repository = InMemorySocRepository()
    run = SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
    ).analyze(_sample("pingan_legacy_apt.json"))
    item = ReviewQueueItem(
        run_id=run.run_id,
        alert_id=run.alert_id,
        reason="fact_conflict",
        verdict=run.decision.verdict if run.decision is not None else None,
        confidence=run.decision.confidence if run.decision is not None else None,
    )
    repository.save_review_item(item)

    service = SocReviewService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        evidence_repository=repository,
        external_disposition_repository=repository,
        memory_candidate_repository=repository,
    )
    return service, repository, item


def test_soc_review_api_lists_open_items(review_api) -> None:
    service, _, item = review_api

    response = soc_review.list_review_items(
        service=service,
        status=ReviewQueueStatus.OPEN,
        limit=50,
        human_intervention_only=True,
    )

    assert [value.queue_id for value in response.items] == [item.queue_id]
    assert response.items[0].status == ReviewQueueStatus.OPEN


def test_soc_review_api_returns_investigation_context(review_api) -> None:
    service, repository, item = review_api
    repository.save_evidence(
        InvestigationEvidence(
            route="asset.locate",
            action="asset.locate",
            status="success",
            message="Asset location completed.",
            result_payload={"mcp_result": {"company_code": "PA011", "mocked": True}},
            queue_id=item.queue_id,
            run_id=item.run_id,
            alert_id=item.alert_id,
        )
    )
    repository.save_external_disposition(
        SocExternalDispositionRecord(
            event=SocExternalDispositionEvent(
                external_system="zeus",
                external_case_id="ZEUS-CASE-ROUTER-1",
                soc_alert_id=item.alert_id,
                soc_run_id=item.run_id,
                soc_queue_id=item.queue_id,
                external_status="误报关闭",
                external_reason="老工单确认授权测试。",
                updated_at=item.updated_at,
                raw_payload_hash="hash-router-zeus",
            ),
            canonical_status=SocExternalDispositionCanonicalStatus.CLOSED_FALSE_POSITIVE,
            apply_status=SocExternalDispositionApplyStatus.MAPPED,
            idempotency_key="external_disposition:zeus:router-1",
            target_run_id=item.run_id,
            target_alert_id=item.alert_id,
            target_queue_id=item.queue_id,
            matched_by="soc_queue_id",
            apply_reason="external status mapped to a unique local target",
        )
    )
    memory_candidate = SocMemoryService(candidate_repository=repository).propose_candidate(
        _memory_candidate_command(
            run_id=item.run_id,
            alert_id=item.alert_id,
            queue_id=item.queue_id,
        )
    )

    context = soc_review.get_review_context(item.queue_id, service=service)

    assert context.queue_item.queue_id == item.queue_id
    assert context.run.run_id == item.run_id
    assert context.summary is not None
    assert context.summary.run_id == item.run_id
    assert len(context.audit_records) == 1
    assert len(context.action_evidence) == 1
    assert context.action_evidence[0].action == "asset.locate"
    assert len(context.external_dispositions) == 1
    assert context.external_dispositions[0].event.external_system == "zeus"
    assert context.memory_candidates == [memory_candidate]
    assert context.correlation_result is not None
    assert context.domain_triage_results
    assert context.investigation_view is not None
    assert context.investigation_view.counts["action_evidence"] == 1
    assert context.investigation_view.counts["external_dispositions"] == 1
    assert context.investigation_view.counts["memory_candidates"] == 1


def test_soc_review_api_closes_item_with_api_actor(review_api) -> None:
    service, repository, item = review_api

    closed = soc_review.close_review_item(
        item.queue_id,
        soc_review.ReviewQueueCloseRequest(reason="复核完成"),
        FakeRequest({"x-soc-actor-id": "spoofed-user"}, user_id="analyst-api"),
        service=service,
    )

    assert closed.status == ReviewQueueStatus.CLOSED
    assert closed.close_reason == "复核完成"
    assert closed.closed_by is not None
    assert closed.closed_by.actor_id == "analyst-api"
    assert closed.closed_by.surface == EntrySurface.API
    assert repository.get_review_item(item.queue_id).status == ReviewQueueStatus.CLOSED


def test_soc_review_api_rejects_header_only_actor_for_l3_mutation(review_api) -> None:
    service, repository, item = review_api

    with pytest.raises(HTTPException) as exc_info:
        soc_review.close_review_item(
            item.queue_id,
            soc_review.ReviewQueueCloseRequest(reason="header actor attempted close"),
            FakeRequest({"x-soc-actor-id": "header-only"}),
            service=service,
        )

    assert exc_info.value.status_code == 403
    assert repository.get_review_item(item.queue_id).status is ReviewQueueStatus.OPEN


def test_soc_review_api_uses_authenticated_web_actor_over_header(review_api) -> None:
    service, _, item = review_api

    closed = soc_review.close_review_item(
        item.queue_id,
        soc_review.ReviewQueueCloseRequest(reason="复核完成"),
        FakeRequest(
            {
                "x-soc-actor-id": "spoofed-user",
                "x-soc-surface": "web",
                "x-trace-id": "trace-web-1",
                "idempotency-key": "idem-web-1",
            },
            user_id="auth-user-1",
        ),
        service=service,
    )

    assert closed.closed_by is not None
    assert closed.closed_by.actor_id == "auth-user-1"
    assert closed.closed_by.surface == EntrySurface.WEB


def test_soc_review_api_corrects_run_and_closes_open_item(review_api) -> None:
    service, repository, item = review_api

    run = soc_review.correct_review_run(
        item.run_id,
        soc_review.ReviewCorrectionRequest(
            verdict=Verdict.FALSE_POSITIVE,
            confidence=0.93,
            reason="分析师确认是误报",
        ),
        FakeRequest({"x-soc-actor-id": "spoofed-user"}, user_id="analyst-api"),
        service=service,
    )

    assert run.decision is not None
    assert run.decision.verdict == Verdict.FALSE_POSITIVE
    assert run.decision.confidence == 0.93
    assert run.decision.confidence_source is DecisionConfidenceSource.HUMAN_CONFIRMATION
    assert run.decision.confidence_is_calibrated is False
    assert run.decision.policy_version == "soc.correction_policy.v1"
    assert run.decision.confidence_explanation == "Analyst-supplied confirmation strength; not a calibrated probability."
    assert run.corrections[0].actor.surface == EntrySurface.API
    summary = repository.get_alert_summary(run.run_id)
    assert summary is not None
    assert summary.confidence_source is DecisionConfidenceSource.HUMAN_CONFIRMATION
    assert repository.get_review_item(item.queue_id).status == ReviewQueueStatus.CLOSED


def test_soc_review_api_confirms_roles_without_authorizing_action(review_api) -> None:
    service, repository, item = review_api

    revision = soc_review.confirm_review_run_roles(
        item.run_id,
        soc_review.RoleAdjudicationConfirmationRequest(
            roles=[
                HumanConfirmedRole(
                    role=AdjudicatedRoleType.VICTIM,
                    entity_type="ip",
                    value="30.116.114.150",
                    rationale="Analyst confirmed the semantic victim role.",
                )
            ],
            response_targets=[
                HumanConfirmedResponseTarget(
                    action_kind="endpoint.isolate_host",
                    target_type="ip",
                    target_value="30.116.114.150",
                    target_role=AdjudicatedRoleType.IMPACTED_ASSET,
                    rationale="The victim is the impacted asset for this isolation proposal.",
                )
            ],
            reason="Analyst reviewed the observed flow and reverse-connection semantics.",
        ),
        FakeRequest(user_id="analyst-api"),
        service=service,
    )

    assert revision.revision == 1
    assert revision.actor.actor_id == "analyst-api"
    assert revision.response_targets[0].automation_allowed is False
    persisted = repository.get_run(item.run_id)
    assert persisted is not None
    assert persisted.role_adjudication_revisions == [revision]


@pytest.mark.asyncio
async def test_soc_review_api_accepts_server_resolved_lead_agent_message(
    review_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, item = review_api

    async def resolve_message(request, *, thread_id, message_id, queue_id):
        assert thread_id == "thread-soc-1"
        assert message_id == "assistant-message-9"
        assert queue_id == item.queue_id
        return ResolvedSocLeadAgentMessage(
            thread_id=thread_id,
            message_id=message_id,
            agent_name="soc-triage",
            text="Server-resolved analyst conclusion.",
            text_sha256="a" * 64,
            checkpoint_id="checkpoint-9",
            context_provenance=SocLeadAgentReviewContextProvenance(
                artifact_id="LCTX-ACCEPT-1",
                queue_id=item.queue_id,
                run_id=item.run_id,
                alert_id=item.alert_id,
                context_hash="b" * 64,
                skill_context_hash="c" * 64,
                chat_thread_id=thread_id,
                chat_run_id="chat-run-9",
                rendered_char_count=2_048,
                context_created_at=datetime(2026, 8, 6, tzinfo=UTC),
            ),
        )

    monkeypatch.setattr(soc_review, "resolve_soc_lead_agent_message", resolve_message)
    result = await soc_review.accept_lead_agent_conclusion(
        queue_id=item.queue_id,
        thread_id="thread-soc-1",
        body=soc_review.LeadAgentConclusionAcceptanceRequest(
            message_id="assistant-message-9",
            acceptance_reason="Analyst verified the conclusion against the alert evidence.",
        ),
        request=BypassAuthFakeRequest(
            {
                "x-soc-surface": "web",
                "idempotency-key": "accept:web:thread-soc-1:assistant-message-9",
            },
            user_id="analyst-web",
        ),
        service=service,
    )

    assert result.memory_candidate is not None
    candidate = result.memory_candidate
    assert "Server-resolved analyst conclusion." in candidate.content
    assert candidate.source.thread_id == "thread-soc-1"
    assert candidate.source.message_id == "assistant-message-9"
    assert candidate.source.source_surface is EntrySurface.WEB
    assert candidate.source.metadata["message_resolution"] == "gateway_checkpoint_state"
    assert candidate.source.metadata["checkpoint_id"] == "checkpoint-9"
    assert candidate.source.metadata["message_text_sha256"] == "a" * 64
    assert candidate.source.metadata["review_context_artifact_id"] == "LCTX-ACCEPT-1"
    assert candidate.source.metadata["review_context_hash"] == "b" * 64
    assert candidate.source.metadata["review_context_skill_hash"] == "c" * 64
    assert candidate.source.metadata["review_context_chat_run_id"] == "chat-run-9"
    assert candidate.status is SocMemoryCandidateStatus.PENDING_REVIEW
    assert candidate.runtime_decision_allowed is False


@pytest.mark.asyncio
async def test_soc_review_api_requires_idempotency_for_lead_agent_acceptance(
    review_api,
) -> None:
    service, _, item = review_api

    with pytest.raises(HTTPException) as exc_info:
        await soc_review.accept_lead_agent_conclusion(
            queue_id=item.queue_id,
            thread_id="thread-soc-1",
            body=soc_review.LeadAgentConclusionAcceptanceRequest(
                message_id="assistant-message-9",
                acceptance_reason="Analyst verified this conclusion.",
            ),
            request=BypassAuthFakeRequest(user_id="analyst-web"),
            service=service,
        )

    assert exc_info.value.status_code == 400


def test_soc_review_api_records_explicit_disposition_outcome_with_authenticated_actor() -> None:
    class RecordingDispositionEvaluationService:
        command = None
        context = None

        def record_outcome(self, command, *, context):
            self.command = command
            self.context = context
            return {"recorded": True}

    service = RecordingDispositionEvaluationService()
    result = soc_review.record_disposition_outcome(
        soc_review.DispositionOutcomeRecordRequest(
            proposal_id="DPROP-API-1",
            observed_disposition=SocOperationalDisposition.CLOSED_BENIGN_TRUE_POSITIVE,
            review_kind=SocDispositionOutcomeReviewKind.SAMPLED_QUALITY_REVIEW,
            sample_id="DSAMPLE-API-1",
            reason="Independent reviewer confirmed the operational disposition.",
            evidence_refs=["review_queue:REV-API-1"],
        ),
        FakeRequest(
            {
                "x-soc-actor-id": "spoofed-user",
                "x-soc-surface": "web",
                "idempotency-key": "outcome:web:api-1",
            },
            user_id="auth-reviewer-1",
        ),
        service=service,
    )

    assert result == {"recorded": True}
    assert service.command.proposal_id == "DPROP-API-1"
    assert service.command.source is SocDispositionOutcomeSource.ANALYST
    assert service.command.sample_id == "DSAMPLE-API-1"
    assert service.command.idempotency_key == "outcome:web:api-1"
    assert service.context.actor.actor_id == "auth-reviewer-1"
    assert service.context.actor.surface == EntrySurface.WEB


def test_soc_review_api_requires_idempotency_header_for_disposition_outcome() -> None:
    with pytest.raises(HTTPException) as exc_info:
        soc_review.record_disposition_outcome(
            soc_review.DispositionOutcomeRecordRequest(
                proposal_id="DPROP-API-1",
                observed_disposition=SocOperationalDisposition.UNKNOWN,
                reason="Insufficient evidence for a terminal operational disposition.",
            ),
            FakeRequest({"x-soc-actor-id": "analyst-api"}),
            service=object(),
        )

    assert exc_info.value.status_code == 400


def test_soc_review_api_exposes_sample_campaign_and_authenticated_reviewer_inbox() -> None:
    class RecordingDispositionEvaluationService:
        reviewer_actor_id = None
        offset = None
        limit = None

        def list_sample_review_campaigns(self, *, limit):
            self.limit = limit
            return {"items": ["DSAMPLE-API-1"], "limit": limit}

        def get_sample_review_inbox(
            self,
            sample_id,
            *,
            reviewer_actor_id,
            offset,
            limit,
        ):
            self.reviewer_actor_id = reviewer_actor_id
            self.offset = offset
            self.limit = limit
            return {"sample_id": sample_id, "reviewer_actor_id": reviewer_actor_id}

    service = RecordingDispositionEvaluationService()

    campaigns = soc_review.list_disposition_sample_campaigns(service=service, limit=25)
    inbox = soc_review.get_disposition_sample_review_inbox(
        "DSAMPLE-API-1",
        FakeRequest(
            {
                "x-soc-actor-id": "spoofed-reviewer",
                "x-soc-surface": "web",
            },
            user_id="auth-qa-reviewer",
        ),
        service=service,
        offset=10,
        limit=20,
    )

    assert campaigns == {"items": ["DSAMPLE-API-1"], "limit": 25}
    assert inbox == {
        "sample_id": "DSAMPLE-API-1",
        "reviewer_actor_id": "auth-qa-reviewer",
    }
    assert service.reviewer_actor_id == "auth-qa-reviewer"
    assert service.offset == 10
    assert service.limit == 20


def test_soc_review_api_missing_item_returns_404(review_api) -> None:
    service, _, _ = review_api

    with pytest.raises(HTTPException) as exc_info:
        soc_review.get_review_context("REV-MISSING", service=service)

    assert exc_info.value.status_code == 404


def test_soc_review_router_exposes_mvp_paths() -> None:
    paths = {route.path for route in soc_review.router.routes}

    assert "/api/soc/review/items" in paths
    assert "/api/soc/review/items/{queue_id}/context" in paths
    assert "/api/soc/review/items/{queue_id}/lead-agent-threads/{thread_id}/accept" in paths
    assert "/api/soc/review/items/{queue_id}/close" in paths
    assert "/api/soc/review/runs/{run_id}/correct" in paths
    assert "/api/soc/review/disposition-outcomes" in paths
    assert "/api/soc/review/disposition-samples" in paths
    assert "/api/soc/review/disposition-samples/{sample_id}/inbox" in paths


def _memory_candidate_command(
    *,
    run_id: str,
    alert_id: str,
    queue_id: str,
) -> SocMemoryCandidateCreateCommand:
    return SocMemoryCandidateCreateCommand(
        candidate_type=SocMemoryCandidateType.BENIGN_PATTERN,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary="Review context memory candidate",
        content="External feedback should remain pending until an analyst reviews it.",
        tenant_scope="pingan",
        tenant_id="pingan",
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.MANUAL_NOTE,
            source_id="review-router-memory-test",
            run_id=run_id,
            alert_id=alert_id,
            queue_id=queue_id,
        ),
        evidence_refs=["manual:review-router-test"],
        validity=SocMemoryCandidateValidity(notes="Review router test candidate."),
        idempotency_key=f"memory:review-router:{queue_id}",
        confidence=0.6,
        facets={"tenant": ["pingan"]},
        decision_impact=SocMemoryDecisionImpact.REVIEW_HINT,
        review_owner="soc_analyst",
        labels=["candidate-only"],
    )
