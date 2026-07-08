from __future__ import annotations

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
    SocMemoryDecisionImpact,
    SocMemoryQuery,
    SocMemoryRecordStatus,
    SocMemoryTargetArtifact,
)
from soc_agent.core import SocMemoryService
from soc_agent.memory import InMemoryMemoryCandidateRepository


class FakeRequest:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {
            "x-soc-actor-id": "soc-web-test",
            "x-soc-surface": "web",
            "x-trace-id": "soc-memory-router-test",
        }
        self.state = SimpleNamespace()


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
    service = SocMemoryService(candidate_repository=repository, record_repository=repository)
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

    repository.save_memory_record(result.memory_record.model_copy(update={"retrieval_enabled": True}))
    enabled_search = soc_memory.search_memory_records(
        SocMemoryQuery(facets={"tenant": ["pingan"]}, text_terms=["feedback"]),
        service=service,
    )
    assert enabled_search.returned_count == 1
    assert enabled_search.matches[0].memory_id == result.memory_record.memory_id


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
