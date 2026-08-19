from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gateway.routers import soc_memory
from soc_agent.contracts import (
    SocMemoryApplicabilitySpec,
    SocMemoryBusinessLesson,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateStatus,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionDirective,
    SocMemoryDecisionEffect,
    SocMemoryDecisionImpact,
    SocMemoryQuery,
    SocMemoryRecordStatus,
    SocMemoryRetrievalActivationAction,
    SocMemoryReviewEffect,
    SocMemoryRevisionProposal,
    SocMemoryRevisionProposalStatus,
    SocMemoryRevisionReviewDecision,
    SocMemoryRevisionReviewResult,
    SocMemoryTargetArtifact,
    Verdict,
)
from soc_agent.core import SocMemoryService
from soc_agent.memory import InMemoryMemoryCandidateRepository


class FakeRequest:
    def __init__(
        self,
        *,
        authenticated: bool = True,
        system_role: str = "user",
    ) -> None:
        self.headers: dict[str, str] = {
            "x-soc-actor-id": "spoofed-user",
            "x-soc-surface": "web",
            "x-trace-id": "soc-memory-router-test",
        }
        self.state = SimpleNamespace()
        if authenticated:
            self.state.auth_source = "session"
            self.state.user = SimpleNamespace(id="soc-web-test", system_role=system_role)


def test_soc_memory_api_lists_candidates_by_review_filters() -> None:
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository)
    candidate = service.propose_candidate(_memory_candidate_command())
    service.propose_candidate(
        _memory_candidate_command(
            run_id="RUN-OTHER",
            alert_id="ALT-OTHER",
            queue_id="REV-OTHER",
            idempotency_key="memory:router:other",
        )
    )

    response = soc_memory.list_memory_candidates(
        service=service,
        status=SocMemoryCandidateStatus.PENDING_REVIEW,
        tenant_scope="pingan",
        tenant_id="pingan",
        run_id="RUN-ROUTER-1",
        alert_id=None,
        queue_id=None,
        limit=50,
    )

    assert response.items == [candidate]


def test_soc_memory_api_gets_candidate() -> None:
    service = SocMemoryService(candidate_repository=InMemoryMemoryCandidateRepository())
    candidate = service.propose_candidate(_memory_candidate_command())

    loaded = soc_memory.get_memory_candidate(candidate.candidate_id, service=service)

    assert loaded == candidate


def test_soc_memory_api_returns_404_for_missing_candidate() -> None:
    service = SocMemoryService(candidate_repository=InMemoryMemoryCandidateRepository())

    with pytest.raises(HTTPException) as exc_info:
        soc_memory.get_memory_candidate("MC-MISSING", service=service)

    assert exc_info.value.status_code == 404


def test_soc_memory_api_reviews_candidate_and_lists_record() -> None:
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
        mutation_audit_repository=repository,
    )
    candidate = service.propose_candidate(_memory_candidate_command())

    result = soc_memory.review_memory_candidate(
        candidate.candidate_id,
        soc_memory.MemoryCandidateReviewRequest(
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Router analyst confirmed candidate.",
        ),
        request=FakeRequest(),
        service=service,
    )

    assert result.candidate.status is SocMemoryCandidateStatus.CONFIRMED
    assert result.memory_record is not None
    assert result.memory_record.status is SocMemoryRecordStatus.CONFIRMED
    assert result.memory_record.created_by.actor_id == "soc-web-test"
    records = soc_memory.list_memory_records(
        service=service,
        status=SocMemoryRecordStatus.CONFIRMED,
        tenant_scope="pingan",
        tenant_id=None,
        source_candidate_id=candidate.candidate_id,
        retrieval_enabled=None,
        limit=50,
    )
    assert records.items == [result.memory_record]
    assert soc_memory.get_memory_record(result.memory_record.memory_id, service=service) == result.memory_record

    disabled_search = soc_memory.search_memory_records(
        SocMemoryQuery(facets={"tenant": ["pingan"]}),
        service=service,
    )
    assert disabled_search.matches == []
    assert disabled_search.skipped_retrieval_disabled == 1

    activation = soc_memory.update_memory_retrieval_activation(
        result.memory_record.memory_id,
        soc_memory.MemoryRetrievalActivationRequest(
            action=SocMemoryRetrievalActivationAction.ENABLE,
            expected_record_version=result.memory_record.version,
            reason="Memory governor approved bounded retrieval.",
            activation_valid_until=datetime.now(UTC) + timedelta(days=90),
            review_after_days=30,
        ),
        request=FakeRequest(system_role="admin"),
        service=service,
    )
    assert activation.record.version == 2
    assert activation.record.retrieval_enabled is True
    assert activation.audit_id is not None
    enabled_search = soc_memory.search_memory_records(
        SocMemoryQuery(facets={"tenant": ["pingan"]}, text_terms=["feedback"]),
        service=service,
    )
    assert enabled_search.returned_count == 1
    assert enabled_search.matches[0].memory_id == result.memory_record.memory_id


def test_soc_memory_api_preserves_reviewed_decision_directive() -> None:
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
        mutation_audit_repository=repository,
    )
    candidate = service.propose_candidate(
        _memory_candidate_command(
            decision_impact=SocMemoryDecisionImpact.DETECTION_DECISION,
        )
    )
    directive = SocMemoryDecisionDirective(
        effect=SocMemoryDecisionEffect.OVERRIDE,
        target_verdict=Verdict.FALSE_POSITIVE,
        review_effect=SocMemoryReviewEffect.CLEAR,
        minimum_match_score=0.8,
        required_facet_keys=["tenant"],
        rationale="Reviewed tenant-scoped false-positive pattern.",
    )
    lesson = SocMemoryBusinessLesson(
        conclusion="Reviewer approved this bounded tenant-scoped false-positive pattern.",
        business_rationale=["The reviewer verified the candidate evidence and tenant context."],
        applicability_conditions=["Every reviewed required tenant facet must match."],
        generalization_boundaries=["Unreviewed optional dimensions may vary but cannot establish a match."],
        invalidation_conditions=["A required facet mismatch or current counterevidence invalidates the lesson."],
        handling_guidance=["Apply the reviewed verdict only after all directive gates pass."],
    )

    result = soc_memory.review_memory_candidate(
        candidate.candidate_id,
        soc_memory.MemoryCandidateReviewRequest(
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Reviewer approved a bounded decision directive.",
            record_lesson=lesson,
            record_applicability=candidate.applicability,
            decision_directive=directive,
        ),
        request=FakeRequest(),
        service=service,
    )

    assert result.memory_record is not None
    assert result.memory_record.decision_impact is SocMemoryDecisionImpact.DETECTION_DECISION
    assert result.memory_record.applicability == candidate.applicability
    assert result.memory_record.decision_directive == directive
    assert result.memory_record.business_lesson == lesson
    assert result.memory_record.metadata["business_lesson_source"] == ("reviewer_supplied")


def test_soc_memory_api_reopens_rejected_candidate() -> None:
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
        mutation_audit_repository=repository,
    )
    candidate = service.propose_candidate(_memory_candidate_command())
    rejected = soc_memory.review_memory_candidate(
        candidate.candidate_id,
        soc_memory.MemoryCandidateReviewRequest(
            decision=SocMemoryCandidateReviewDecision.REJECT,
            reason="Reviewer declined candidate persistence.",
        ),
        request=FakeRequest(),
        service=service,
    )

    reopened = soc_memory.review_memory_candidate(
        candidate.candidate_id,
        soc_memory.MemoryCandidateReviewRequest(
            decision=SocMemoryCandidateReviewDecision.REOPEN,
            reason="Reviewer reopened the candidate.",
        ),
        request=FakeRequest(),
        service=service,
    )

    assert rejected.candidate.status is SocMemoryCandidateStatus.REJECTED
    assert reopened.previous_status is SocMemoryCandidateStatus.REJECTED
    assert reopened.candidate.status is SocMemoryCandidateStatus.PENDING_REVIEW
    assert reopened.memory_record is None


def test_soc_memory_api_supersedes_same_alert_profile_candidate() -> None:
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(
        candidate_repository=repository,
        mutation_audit_repository=repository,
    )
    original_command = _memory_candidate_command(
        idempotency_key="memory:router:profile-v1",
        decision_impact=SocMemoryDecisionImpact.DETECTION_DECISION,
    )
    assert original_command.applicability is not None
    original_command = original_command.model_copy(
        update={
            "source": original_command.source.model_copy(
                update={
                    "source_type": SocMemoryCandidateSourceType.REPEATED_PATTERN,
                    "metadata": {
                        "environment": "dev",
                        "data_class": "simulation",
                    },
                }
            )
        }
    )
    original = service.propose_candidate(original_command)
    upgraded_command = original_command.model_copy(
        update={
            "idempotency_key": "memory:router:profile-v2",
            "source": original_command.source.model_copy(update={"source_id": "router-memory-test-v2"}),
            "applicability": original_command.applicability.model_copy(
                update={
                    "profile_version": "2",
                    "feature_schema_version": "soc.memory_features.generic.v2",
                }
            ),
        }
    )
    successor = service.propose_candidate(upgraded_command)

    result = soc_memory.supersede_memory_candidate(
        original.candidate_id,
        soc_memory.MemoryCandidateSupersessionRequest(
            successor_candidate_id=successor.candidate_id,
            reason="Profile v2 replaces the same-alert v1 review candidate.",
        ),
        request=FakeRequest(system_role="admin"),
        service=service,
    )

    assert result.candidate.status is SocMemoryCandidateStatus.SUPERSEDED
    assert result.candidate.superseded_by_candidate_id == successor.candidate_id
    assert result.successor.metadata["supersedes_candidate_ids"] == [original.candidate_id]


def test_soc_memory_api_persists_explicit_business_lesson() -> None:
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
    )
    candidate = service.propose_candidate(
        _memory_candidate_command(
            idempotency_key="memory:router:explicit-lesson",
            decision_impact=SocMemoryDecisionImpact.DETECTION_DECISION,
        )
    )
    lesson = SocMemoryBusinessLesson(
        conclusion="运营确认该精确内部服务调用属于可复用的业务误报模式。",
        business_rationale=["当前候选证据与内部服务登记信息一致。"],
        applicability_conditions=["必须命中审核后的全部 canonical required facets。"],
        generalization_boundaries=["未列为必需的实体可以变化，但不能独立触发改判。"],
        invalidation_conditions=["必需 facet 缺失或当前证据出现反证时失效。"],
        handling_guidance=["精确适用时复用误报结论，否则重新研判。"],
    )

    result = soc_memory.review_memory_candidate(
        candidate.candidate_id,
        soc_memory.MemoryCandidateReviewRequest(
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Reviewer supplied a complete reusable business lesson.",
            record_lesson=lesson,
            record_applicability=candidate.applicability,
            confirmed_verdict=Verdict.FALSE_POSITIVE,
            apply_to_future_matches=True,
        ),
        request=FakeRequest(),
        service=service,
    )

    assert result.memory_record is not None
    assert result.memory_record.business_lesson == lesson
    assert result.memory_record.summary == lesson.conclusion
    assert "业务依据 / Business rationale" in result.memory_record.content
    assert result.memory_record.metadata["business_lesson_source"] == ("reviewer_supplied")


def test_soc_memory_api_rejects_future_decision_without_business_lesson() -> None:
    with pytest.raises(ValueError, match="explicit reviewed record_lesson"):
        soc_memory.MemoryCandidateReviewRequest(
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Reviewed simulation lesson for Reverse connection detector.",
            confirmed_verdict=Verdict.FALSE_POSITIVE,
            apply_to_future_matches=True,
        )


def test_soc_memory_api_rejects_analyst_retrieval_activation() -> None:
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, record_repository=repository)
    candidate = service.propose_candidate(_memory_candidate_command())
    reviewed = soc_memory.review_memory_candidate(
        candidate.candidate_id,
        soc_memory.MemoryCandidateReviewRequest(
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Confirm before activation authorization test.",
        ),
        request=FakeRequest(),
        service=service,
    )
    assert reviewed.memory_record is not None

    with pytest.raises(HTTPException) as exc_info:
        soc_memory.update_memory_retrieval_activation(
            reviewed.memory_record.memory_id,
            soc_memory.MemoryRetrievalActivationRequest(
                action=SocMemoryRetrievalActivationAction.ENABLE,
                expected_record_version=reviewed.memory_record.version,
                reason="Analyst attempted activation.",
                activation_valid_until=datetime.now(UTC) + timedelta(days=90),
                review_after_days=30,
            ),
            request=FakeRequest(),
            service=service,
        )

    assert exc_info.value.status_code == 403
    assert service.get_record(reviewed.memory_record.memory_id).retrieval_enabled is False


def test_soc_memory_api_rejects_untrusted_candidate_review() -> None:
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, record_repository=repository)
    candidate = service.propose_candidate(_memory_candidate_command())

    with pytest.raises(HTTPException) as exc_info:
        soc_memory.review_memory_candidate(
            candidate.candidate_id,
            soc_memory.MemoryCandidateReviewRequest(
                decision=SocMemoryCandidateReviewDecision.REJECT,
                reason="untrusted caller attempted review",
            ),
            request=FakeRequest(authenticated=False),
            service=service,
        )

    assert exc_info.value.status_code == 403
    assert service.get_candidate(candidate.candidate_id).status is SocMemoryCandidateStatus.PENDING_REVIEW


def test_soc_memory_api_lists_and_reviews_revision_proposals() -> None:
    proposal = SocMemoryRevisionProposal(
        proposal_id="MRP-ROUTER-001",
        idempotency_key="memory-revision:router:001",
        memory_id="MEM-ROUTER-001",
        memory_version=2,
        source_feedback_id="MF-ROUTER-001",
        reason="A trusted final outcome contradicted this reviewed Memory.",
    )

    class FakeEvolutionService:
        def __init__(self) -> None:
            self.review_context = None

        def list_revision_proposals(self, **_: object) -> list[SocMemoryRevisionProposal]:
            return [proposal]

        def get_revision_proposal(self, proposal_id: str) -> SocMemoryRevisionProposal:
            assert proposal_id == proposal.proposal_id
            return proposal

        def review_revision_proposal(self, command, *, context):
            self.review_context = context
            reviewed = proposal.model_copy(
                update={
                    "status": SocMemoryRevisionProposalStatus.ACCEPTED,
                    "reviewed_by": context.actor.actor_id,
                    "review_reason": command.reason,
                }
            )
            return SocMemoryRevisionReviewResult(
                proposal=reviewed,
                previous_status=SocMemoryRevisionProposalStatus.PENDING_REVIEW,
                decision=command.decision,
            )

    service = FakeEvolutionService()
    listed = soc_memory.list_memory_revision_proposals(
        service=service,
        memory_id=proposal.memory_id,
        status=SocMemoryRevisionProposalStatus.PENDING_REVIEW,
        limit=50,
    )
    loaded = soc_memory.get_memory_revision_proposal(
        proposal.proposal_id,
        service=service,
    )
    reviewed = soc_memory.review_memory_revision_proposal(
        proposal.proposal_id,
        soc_memory.MemoryRevisionReviewRequest(
            decision=SocMemoryRevisionReviewDecision.ACCEPT,
            reason="Reviewer confirmed the contradiction; keep old Memory suspended.",
        ),
        request=FakeRequest(system_role="admin"),
        service=service,
    )

    assert listed.items == [proposal]
    assert loaded == proposal
    assert reviewed.proposal.status is SocMemoryRevisionProposalStatus.ACCEPTED
    assert reviewed.memory_record_changed is False
    assert reviewed.retrieval_reenabled is False
    assert service.review_context.actor.actor_id == "soc-web-test"
    assert "soc_admin" in service.review_context.actor.roles


def _memory_candidate_command(
    *,
    run_id: str = "RUN-ROUTER-1",
    alert_id: str = "ALT-ROUTER-1",
    queue_id: str = "REV-ROUTER-1",
    idempotency_key: str = "memory:router:1",
    decision_impact: SocMemoryDecisionImpact = SocMemoryDecisionImpact.REVIEW_HINT,
) -> SocMemoryCandidateCreateCommand:
    return SocMemoryCandidateCreateCommand(
        candidate_type=SocMemoryCandidateType.DETECTION_LESSON,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary="Router memory candidate",
        content="External or analyst feedback must remain pending until reviewed.",
        tenant_scope="pingan",
        tenant_id="pingan",
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.MANUAL_NOTE,
            source_id="router-memory-test",
            run_id=run_id,
            alert_id=alert_id,
            queue_id=queue_id,
        ),
        evidence_refs=["manual:router-test"],
        validity=SocMemoryCandidateValidity(notes="Router test candidate."),
        idempotency_key=idempotency_key,
        confidence=0.7,
        facets={"tenant": ["pingan"]},
        applicability=(
            SocMemoryApplicabilitySpec(
                profile_id="soc.generic",
                profile_version="1",
                feature_schema_version="soc.memory_features.generic.v1",
                required_facets={"tenant": ["pingan"]},
            )
            if decision_impact is SocMemoryDecisionImpact.DETECTION_DECISION
            else None
        ),
        decision_impact=decision_impact,
        review_owner="soc_analyst",
        labels=["candidate-only"],
    )
