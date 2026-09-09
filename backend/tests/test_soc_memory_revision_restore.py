from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test_soc_memory_revision_workflow import _active_memory_fixture, _reviewer_context

from soc_agent.contracts import (
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateStatus,
    SocMemoryRetrievalActivationCommand,
    SocMemoryRevisionCandidateCreateCommand,
    SocMemoryRevisionIssueType,
    SocMutationOperation,
)
from soc_agent.core import SocMemoryService, SocServiceConflictError, SocServiceError
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables


@pytest.fixture
def revision(tmp_path):
    now = datetime(2026, 9, 9, 8, tzinfo=UTC)
    engine = create_engine(f"sqlite:///{tmp_path / 'restore.sqlite'}")
    create_soc_tables(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlAlchemyAlertRepository(factory)
    service, _, memory_id = _active_memory_fixture(repository, now=now)
    old = service.get_record(memory_id)
    candidate = service.propose_revision_candidate(
        SocMemoryRevisionCandidateCreateCommand(memory_id=memory_id, expected_record_version=old.version, issue_type=SocMemoryRevisionIssueType.LESSON_INCOMPLETE, reason="The operator is considering improvements to this business lesson."),
        context=_reviewer_context(key="restore:create"),
    ).candidate
    yield service, repository, factory, now, old, candidate
    engine.dispose()


def restore_command(candidate, record, now):
    return SocMemoryCandidateReviewCommand(
        candidate_id=candidate.candidate_id,
        decision="reject",
        reason="Operator cancels this revision and explicitly restores the unchanged reviewed lesson.",
        restore_predecessor=True,
        expected_predecessor_version=record.version,
        activation_valid_until=now + timedelta(days=30),
        activation_review_after_days=15,
    )


@pytest.mark.parametrize("already_rejected", [False, True])
@pytest.mark.parametrize("directive", [False, True])
def test_restore_preserves_lesson_scope_and_mode_with_idempotent_audit(revision, already_rejected, directive):
    service, repository, _, now, old, candidate = revision
    record = service.get_record(old.memory_id)
    if not directive:
        repository.save_memory_record(record.model_copy(update={"decision_directive": None}))
    if already_rejected:
        service.review_candidate(SocMemoryCandidateReviewCommand(candidate_id=candidate.candidate_id, decision="reject", reason="Abandon revision without restoring."), context=_reviewer_context(key="restore:reject"))
    before = service.get_record(old.memory_id)
    command = restore_command(candidate, before, now)
    context = _reviewer_context(key="restore:combined")
    result = service.review_candidate(command, context=context)
    assert result == service.review_candidate(command, context=context)
    restored = service.get_record(old.memory_id)
    assert result.restored_predecessor_record == restored
    assert restored.retrieval_enabled is True
    assert restored.metadata["revision_pending"] is False
    assert restored.content_hash == before.content_hash
    assert restored.facets_hash == before.facets_hash
    assert restored.decision_directive == before.decision_directive
    assert restored.business_lesson == before.business_lesson
    assert result.memory_record is None
    assert result.candidate.status is SocMemoryCandidateStatus.REJECTED
    for operation in (SocMutationOperation.MEMORY_REVIEW, SocMutationOperation.MEMORY_RETRIEVAL_ACTIVATION):
        assert repository.find_mutation_audit_by_idempotency_key(operation, "restore:combined") is not None


@pytest.mark.parametrize("problem", ["stale_version", "expired_window", "other_revision", "changed_content", "expired_record", "not_revision"])
def test_restore_failure_leaves_candidate_and_record_unchanged(revision, problem):
    service, repository, _, now, old, candidate = revision
    record = service.get_record(old.memory_id)
    command = restore_command(candidate, record, now)
    if problem == "stale_version":
        command = command.model_copy(update={"expected_predecessor_version": record.version + 1})
    elif problem == "expired_window":
        command = command.model_copy(update={"activation_valid_until": now - timedelta(days=1)})
    elif problem == "other_revision":
        repository.save_memory_candidate(candidate.model_copy(update={"revision_lineage": candidate.revision_lineage.model_copy(update={"suspended_record_version": record.version - 1})}))
    elif problem == "expired_record":
        repository.save_memory_record(record.model_copy(update={"validity": record.validity.model_copy(update={"valid_from": now - timedelta(days=60), "valid_until": now - timedelta(days=1)})}))
    elif problem == "not_revision":
        repository.save_memory_candidate(candidate.model_copy(update={"revision_lineage": None}))
    else:
        repository.save_memory_record(record.model_copy(update={"content_hash": "changed-content"}))
    before_record, before_candidate = service.get_record(old.memory_id), service.get_candidate(candidate.candidate_id)
    with pytest.raises((SocServiceError, SocServiceConflictError)):
        service.review_candidate(command, context=_reviewer_context(key="restore:invalid"))
    assert service.get_record(old.memory_id) == before_record
    assert service.get_candidate(candidate.candidate_id) == before_candidate
    assert repository.find_mutation_audit_by_idempotency_key(SocMutationOperation.MEMORY_REVIEW, "restore:invalid") is None


@pytest.mark.parametrize("write_number", [1, 2, 3, 4])
def test_restore_is_atomic_on_write_failure(revision, write_number):
    service, _, factory, now, old, candidate = revision
    before = service.get_record(old.memory_id)

    def fail(count):
        if count == write_number:
            raise RuntimeError("injected restore failure")

    repository = SqlAlchemyAlertRepository(factory, mutation_write_hook=fail)
    failing = SocMemoryService(candidate_repository=repository, record_repository=repository, mutation_audit_repository=repository, now_provider=lambda: now)
    with pytest.raises(RuntimeError, match="injected restore failure"):
        failing.review_candidate(restore_command(candidate, before, now), context=_reviewer_context(key="restore:failure"))
    assert service.get_record(old.memory_id) == before
    assert service.get_candidate(candidate.candidate_id) == candidate


def test_restore_requires_explicit_version_and_reject_decision():
    for fields in ({}, {"decision": "confirm", "expected_predecessor_version": 2}):
        with pytest.raises(ValidationError):
            SocMemoryCandidateReviewCommand(candidate_id="MC-test", reason="explicit restore", restore_predecessor=True, **{"decision": "reject", **fields})


def test_restoration_retry_cannot_undo_a_later_pause(revision):
    service, _, _, now, old, candidate = revision
    command = restore_command(candidate, service.get_record(old.memory_id), now)
    context = _reviewer_context(key="restore:retry-after-pause")
    restored = service.review_candidate(command, context=context).restored_predecessor_record
    service.set_retrieval_activation(
        SocMemoryRetrievalActivationCommand(memory_id=old.memory_id, action="disable", expected_record_version=restored.version, reason="A later operator intentionally paused this memory."),
        context=_reviewer_context(key="restore:later-pause"),
    )
    with pytest.raises(SocServiceConflictError, match="has changed"):
        service.review_candidate(command, context=context)
    assert service.get_record(old.memory_id).retrieval_enabled is False


def test_api_restores_through_shared_service_with_authenticated_authority(revision):
    from test_soc_memory_router import FakeRequest

    from app.gateway.routers import soc_memory

    service, _, _, now, old, candidate = revision
    command = restore_command(candidate, service.get_record(old.memory_id), now)
    result = soc_memory.review_memory_candidate(
        candidate.candidate_id,
        soc_memory.MemoryCandidateReviewRequest(**command.model_dump(exclude={"candidate_id"})),
        request=FakeRequest(system_role="admin", idempotency_key="restore:api"),
        service=service,
    )
    assert result.restored_predecessor_record.retrieval_enabled is True
    assert result.restored_predecessor_record.retrieval_updated_by.actor_id == "soc-web-test"


def test_rejected_revision_cannot_restore_over_a_new_revision(revision):
    service, _, _, now, old, candidate = revision
    service.review_candidate(SocMemoryCandidateReviewCommand(candidate_id=candidate.candidate_id, decision="reject", reason="Only abandon this revision."), context=_reviewer_context(key="restore:first-reject"))
    service.propose_revision_candidate(
        SocMemoryRevisionCandidateCreateCommand(
            memory_id=old.memory_id, expected_record_version=service.get_record(old.memory_id).version, issue_type=SocMemoryRevisionIssueType.LESSON_INCOMPLETE, reason="A different reviewer opened a new revision."
        ),
        context=_reviewer_context(key="restore:second-revision"),
    )
    before = service.get_record(old.memory_id)
    with pytest.raises(SocServiceConflictError, match="another operation"):
        service.review_candidate(restore_command(candidate, before, now), context=_reviewer_context(key="restore:old-revision"))
    assert service.get_record(old.memory_id) == before
