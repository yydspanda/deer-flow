from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest

from soc_agent.contracts import (
    ActorContext,
    SocMemoryApplicabilitySpec,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateStatus,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryRecord,
    SocMemoryRecordStatus,
    SocMemoryRevisionCandidateCreateCommand,
    SocMemoryRevisionIssueType,
    SocMemoryTargetArtifact,
)
from soc_agent.core import SocMemoryService
from soc_agent.memory import InMemoryMemoryCandidateRepository
from soc_agent.memory.governance import scope_identity
from soc_agent.memory.learning import learning_view, resolve_learning_candidate

NOW = datetime(2026, 9, 16, tzinfo=UTC)


def command(index=1, *, automatic=False, window="a", behavior="shell", data_class="operational"):
    return SocMemoryCandidateCreateCommand(
        candidate_type=SocMemoryCandidateType.DETECTION_LESSON,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary="Synthetic recurring lesson",
        content="Synthetic learning coordination test.",
        tenant_scope="tenant-a",
        tenant_id="tenant-a",
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.REPEATED_PATTERN if automatic else SocMemoryCandidateSourceType.MANUAL_NOTE,
            source_id=f"memory_pattern:{window * 64}" if automatic else f"manual:{index}",
            run_id=f"RUN-{index}",
            alert_id=f"ALERT-{index}",
            metadata={"data_class": data_class},
        ),
        evidence_refs=[f"run:RUN-{index}"],
        idempotency_key=f"learning:{automatic}:{index}",
        validity=SocMemoryCandidateValidity(valid_from=NOW, notes="Synthetic validity"),
        facets={"behavior_fingerprint": [behavior], "environment": ["dev"], "entity": ["dst_ip:10.0.0.1"]},
        applicability=SocMemoryApplicabilitySpec(
            profile_id="test",
            profile_version="1",
            feature_schema_version="test.v1",
            required_facets={"behavior_fingerprint": [behavior], "environment": ["dev"]},
            minimum_strong_anchor_matches=1,
        ),
        metadata={"lineage_key": behavior, "aggregation_key": window * 64, **({} if automatic else {"data_class": data_class})},
    )


def record(repository, candidate, *, enabled=True, **changes):
    candidate = candidate.model_copy(update={"status": SocMemoryCandidateStatus.CONFIRMED})
    repository.save_memory_candidate(candidate)
    result = SocMemoryRecord(
        memory_type=candidate.candidate_type,
        target_artifact=candidate.target_artifact,
        tenant_scope=candidate.tenant_scope,
        tenant_id=candidate.tenant_id,
        source_candidate_id=candidate.candidate_id,
        source=candidate.source,
        summary=candidate.summary,
        content=candidate.content,
        facets=candidate.facets,
        applicability=candidate.applicability,
        evidence_refs=candidate.evidence_refs,
        validity=candidate.validity,
        content_hash="test",
        facets_hash="test",
        retrieval_enabled=enabled,
        created_by=ActorContext(actor_id="reviewer"),
        metadata=candidate.metadata,
    ).model_copy(update=changes)
    repository.save_memory_record(result)
    return result


def test_scope_identity_is_independent_of_manual_or_automatic_storage_layout():
    assert scope_identity(command()) == scope_identity(command(automatic=True))
    assert scope_identity(command()) != scope_identity(command(data_class="simulation"))


@pytest.mark.parametrize("automatic_first", [False, True])
def test_manual_and_automatic_share_pending_candidate(automatic_first):
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, now_provider=lambda: NOW)
    first = service.propose_candidate(command(automatic=automatic_first), coordinate_learning=True)
    second = service.propose_candidate(command(2, automatic=not automatic_first), coordinate_learning=True)
    assert first.candidate_id == second.candidate_id
    assert len(repository.list_memory_candidates()) == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_confirmed_lesson_is_not_duplicated_when_paused_or_enabled(enabled):
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, now_provider=lambda: NOW)
    first = service.propose_candidate(command(), coordinate_learning=True)
    saved = record(repository, first, enabled=enabled)
    next_candidate = service.propose_candidate(command(2, automatic=True), coordinate_learning=True)
    assert next_candidate.candidate_id == first.candidate_id
    view = learning_view(repository, next_candidate, now=NOW)
    assert view.memory_id == saved.memory_id
    assert view.action == "view_memory"
    assert view.use_mode == ("reference" if enabled else "paused")


@pytest.mark.parametrize("status", [SocMemoryCandidateStatus.REJECTED, SocMemoryCandidateStatus.EXPIRED, SocMemoryCandidateStatus.DEPRECATED])
def test_closed_candidate_only_suppresses_its_own_cohort(status):
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, now_provider=lambda: NOW)
    first = service.propose_candidate(command(), coordinate_learning=True)
    repository.save_memory_candidate(first.model_copy(update={"status": status}))
    same = service.propose_candidate(command(2, automatic=True), coordinate_learning=True)
    assert same.candidate_id == first.candidate_id
    later = service.propose_candidate(command(3, automatic=True, window="b"), coordinate_learning=True)
    assert later.candidate_id != first.candidate_id
    assert later.status is SocMemoryCandidateStatus.PENDING_REVIEW
    assert service.propose_candidate(command(4, automatic=True, window="b"), coordinate_learning=True).candidate_id == later.candidate_id


def test_narrowed_confirmed_scope_does_not_block_uncovered_alerts():
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, now_provider=lambda: NOW)
    first = service.propose_candidate(command(), coordinate_learning=True)
    narrowed = first.applicability.model_copy(update={"required_facets": {**first.applicability.required_facets, "entity": ["dst_ip:10.0.0.2"]}})
    record(repository, first, applicability=narrowed)
    assert resolve_learning_candidate(repository, command(2), now=NOW) is None
    assert service.propose_candidate(command(2), coordinate_learning=True).candidate_id != first.candidate_id


def test_repeated_narrowing_cannot_return_an_inapplicable_idempotency_collision():
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, now_provider=lambda: NOW)
    incoming = command(automatic=True)
    ids = set()
    for _ in range(3):
        candidate = service.propose_candidate(incoming, coordinate_learning=True)
        assert candidate.candidate_id not in ids
        ids.add(candidate.candidate_id)
        narrowed = candidate.applicability.model_copy(update={"required_facets": {**candidate.applicability.required_facets, "entity": ["dst_ip:10.0.0.2"]}})
        record(repository, candidate, applicability=narrowed)


def test_expired_record_is_not_presented_as_active_or_automatically_reenabled():
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, now_provider=lambda: NOW)
    first = service.propose_candidate(command(), coordinate_learning=True)
    saved = record(repository, first, retrieval_valid_until=NOW - timedelta(days=1))
    view = learning_view(repository, repository.get_memory_candidate(first.candidate_id), now=NOW)
    assert view.use_mode == "expired"
    assert repository.get_memory_record(saved.memory_id) == saved


def test_read_only_old_profile_lookup_uses_stored_scope_without_ignoring_narrowing():
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, now_provider=lambda: NOW)
    first = service.propose_candidate(command(), coordinate_learning=True)
    spec = first.applicability
    lookup = command(2).model_copy(
        update={"applicability": None, "metadata": {**command(2).metadata, "memory_profile_id": spec.profile_id, "memory_profile_version": spec.profile_version, "memory_feature_schema_version": spec.feature_schema_version}}
    )
    assert resolve_learning_candidate(repository, lookup, now=NOW).candidate_id == first.candidate_id
    narrowed = spec.model_copy(update={"required_facets": {**spec.required_facets, "entity": ["dst_ip:10.0.0.2"]}})
    record(repository, first, applicability=narrowed)
    assert resolve_learning_candidate(repository, lookup, now=NOW) is None


def test_distinct_behaviors_and_tenants_do_not_coalesce():
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, now_provider=lambda: NOW)
    first = service.propose_candidate(command(), coordinate_learning=True)
    other = service.propose_candidate(command(2, behavior="different"), coordinate_learning=True)
    assert first.candidate_id != other.candidate_id
    assert resolve_learning_candidate(repository, command(3).model_copy(update={"tenant_id": "other"}), now=NOW) is None


def test_open_revision_wins_over_original_memory_and_stale_manual_link():
    from test_soc_memory_revision_workflow import _reviewer_context

    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, record_repository=repository, mutation_audit_repository=repository, now_provider=lambda: NOW)
    first = service.propose_candidate(command(), coordinate_learning=True)
    saved = record(repository, first)
    revision = service.propose_revision_candidate(
        SocMemoryRevisionCandidateCreateCommand(
            memory_id=saved.memory_id,
            expected_record_version=saved.version,
            issue_type=SocMemoryRevisionIssueType.LESSON_INCOMPLETE,
            reason="Synthetic reviewer requests a clearer applicability boundary.",
        ),
        context=_reviewer_context(key="learning:revision"),
    )
    target = service.propose_candidate(command(2, automatic=True), coordinate_learning=True)
    assert target.candidate_id == revision.candidate.candidate_id
    assert learning_view(repository, first, now=NOW).candidate_id == target.candidate_id
    assert learning_view(repository, target, now=NOW).state == "revision_pending"
    assert not repository.get_memory_record(saved.memory_id).retrieval_enabled


def test_old_automatic_key_does_not_block_new_window_after_rejection():
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, now_provider=lambda: NOW)
    first = service.propose_candidate(command(automatic=True), coordinate_learning=True)
    repository.save_memory_candidate(first.model_copy(update={"status": SocMemoryCandidateStatus.REJECTED}))
    later = command(automatic=True, window="b")
    second = service.propose_candidate(later, coordinate_learning=True)
    assert second.candidate_id != first.candidate_id
    assert service.propose_candidate(later, coordinate_learning=True).candidate_id == second.candidate_id


def test_deprecated_memory_does_not_block_later_cohorts():
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, now_provider=lambda: NOW)
    first = service.propose_candidate(command(), coordinate_learning=True)
    record(repository, first, status=SocMemoryRecordStatus.DEPRECATED, enabled=False)
    same = service.propose_candidate(command(2), coordinate_learning=True)
    assert same.candidate_id == first.candidate_id
    assert learning_view(repository, same, now=NOW).use_mode == "retired"
    assert service.propose_candidate(command(3, window="b"), coordinate_learning=True).candidate_id != first.candidate_id


def test_manual_auto_concurrency_is_serialized_in_sqlite(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'learning.sqlite'}", connect_args={"check_same_thread": False, "timeout": 15})
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    gate = Barrier(2)

    def propose(automatic):
        service = SocMemoryService(candidate_repository=repository, record_repository=repository, now_provider=lambda: NOW)
        gate.wait(timeout=10)
        return service.propose_candidate(command(1 if automatic else 2, automatic=automatic), coordinate_learning=True)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(propose, [True, False]))
        assert results[0].candidate_id == results[1].candidate_id
        assert len(repository.list_memory_candidates()) == 1
    finally:
        engine.dispose()


def test_center_prioritizes_pending_revision_over_stored_predecessor():
    from test_soc_memory_revision_workflow import _reviewer_context

    from soc_agent.core.memory_center import _select_governance_objects

    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(candidate_repository=repository, record_repository=repository, mutation_audit_repository=repository, now_provider=lambda: NOW)
    first = service.propose_candidate(command(), coordinate_learning=True)
    saved = record(repository, first)
    revision = service.propose_revision_candidate(
        SocMemoryRevisionCandidateCreateCommand(
            memory_id=saved.memory_id,
            expected_record_version=saved.version,
            issue_type=SocMemoryRevisionIssueType.LESSON_INCOMPLETE,
            reason="Synthetic clarification for analyst review.",
        ),
        context=_reviewer_context(key="center:revision"),
    )
    selected, _ = _select_governance_objects(repository.list_memory_candidates(), {first.candidate_id: saved})
    assert selected.candidate_id == revision.candidate.candidate_id
