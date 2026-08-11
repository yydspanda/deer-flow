from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gateway.routers import soc_memory
from soc_agent.contracts import (
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
    candidate = service.propose_candidate(_memory_candidate_command())
    directive = SocMemoryDecisionDirective(
        effect=SocMemoryDecisionEffect.OVERRIDE,
        target_verdict=Verdict.FALSE_POSITIVE,
        review_effect=SocMemoryReviewEffect.CLEAR,
        minimum_match_score=0.8,
        required_facet_keys=["tenant"],
        rationale="Reviewed tenant-scoped false-positive pattern.",
    )

    result = soc_memory.review_memory_candidate(
        candidate.candidate_id,
        soc_memory.MemoryCandidateReviewRequest(
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Reviewer approved a bounded decision directive.",
            decision_directive=directive,
        ),
        request=FakeRequest(),
        service=service,
    )

    assert result.memory_record is not None
    assert result.memory_record.decision_impact is SocMemoryDecisionImpact.DETECTION_DECISION
    assert result.memory_record.decision_directive == directive


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


def _memory_candidate_command(
    *,
    run_id: str = "RUN-ROUTER-1",
    alert_id: str = "ALT-ROUTER-1",
    queue_id: str = "REV-ROUTER-1",
    idempotency_key: str = "memory:router:1",
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
        decision_impact=SocMemoryDecisionImpact.REVIEW_HINT,
        review_owner="soc_analyst",
        labels=["candidate-only"],
    )
