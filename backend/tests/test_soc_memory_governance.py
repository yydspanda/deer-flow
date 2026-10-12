import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test_soc_memory_patterns import _observe
from test_soc_memory_revision_workflow import _reviewer_context
from test_soc_pingan_memory_profile import _reviewed_business_lesson, _run

from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import (
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryCandidateStatus,
    SocMemoryRecordStatus,
    SocMemoryRunPromotionCommand,
    Verdict,
)
from soc_agent.contracts.memory_patterns import MemoryPatternAccumulationScope
from soc_agent.core import SocMemoryPatternService, SocMemoryService, SocServiceConflictError
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.memory.lineage import memory_pattern_lineage_metadata
from soc_agent.memory.sources import memory_candidate_command_from_run_promotion


@pytest.fixture
def services():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    service = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
        mutation_audit_repository=repository,
        profile_registry=build_soc_memory_profile_registry(),
    )
    yield service, repository
    engine.dispose()


def candidate(service, index, verdict=Verdict.FALSE_POSITIVE, **run_options):
    run = _run(index, verdict=verdict, **run_options)
    command = memory_candidate_command_from_run_promotion(
        run,
        SocMemoryRunPromotionCommand(run_id=run.run_id, metadata={"environment": "prd"}),
        profile_registry=build_soc_memory_profile_registry(),
    )
    return service.propose_candidate(command)


def confirm(service, item, verdict, *, directive=True, activate=True, **options):
    return service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=item.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="Reviewed synthetic scope comparison; not production truth.",
            record_lesson=_reviewed_business_lesson("模拟审核：该反连模式是已核实的内部服务访问，没有真实攻击。" if verdict is Verdict.FALSE_POSITIVE else "模拟复核：新业务事实纠正了先前判断，该适用范围内的反连行为已确认为真实攻击。"),
            confirmed_verdict=verdict,
            apply_to_future_matches=directive,
            activate_retrieval=activate,
            activation_valid_until=datetime.now(UTC) + timedelta(days=30),
            activation_review_after_days=7,
            **options,
        ),
        context=_reviewer_context(key=f"confirm:{item.candidate_id}"),
    ).memory_record


@pytest.mark.parametrize("directive", [True, False])
def test_same_scope_opposite_lessons_cannot_both_be_published(services, directive):
    service, repository = services
    first = confirm(service, candidate(service, 1), Verdict.FALSE_POSITIVE, directive=directive)
    second = candidate(service, 2, Verdict.TRUE_POSITIVE)
    with pytest.raises(SocServiceConflictError, match="经验|Memory"):
        confirm(service, second, Verdict.TRUE_POSITIVE, directive=directive)
    assert repository.get_memory_candidate(second.candidate_id).status is SocMemoryCandidateStatus.PENDING_REVIEW
    assert repository.get_memory_record_by_candidate_id(second.candidate_id) is None
    assert repository.get_memory_record(first.memory_id).retrieval_enabled


def test_review_can_replace_an_opposing_lesson_atomically(services):
    service, repository = services
    first = confirm(service, candidate(service, 1), Verdict.FALSE_POSITIVE)
    second = candidate(service, 2, Verdict.TRUE_POSITIVE)
    result = confirm(
        service,
        second,
        Verdict.TRUE_POSITIVE,
        replaces_memory_id=first.memory_id,
        expected_replaced_version=first.version,
    )
    old = repository.get_memory_record(first.memory_id)
    assert old.status is SocMemoryRecordStatus.DEPRECATED
    assert not old.retrieval_enabled
    assert old.superseded_by_memory_id == result.memory_id
    assert result.retrieval_enabled
    assert result.revision_lineage.predecessor_memory_id == first.memory_id


def test_pending_candidates_are_coalesced_by_scope_not_model_verdict(services):
    service, _ = services
    first = candidate(service, 1)
    second = candidate(service, 2, Verdict.TRUE_POSITIVE)
    assert second.candidate_id == first.candidate_id
    assert len(second.metadata["governance_observations"]) == 1


def test_governance_preview_explains_same_scope_disagreement(services):
    service, _ = services
    first = confirm(service, candidate(service, 1), Verdict.FALSE_POSITIVE)
    second = candidate(service, 2, Verdict.SUSPICIOUS)
    preview = service.preview_candidate_governance(second.candidate_id, reviewer_verdict=Verdict.TRUE_POSITIVE)
    assert preview.recommendation == "revise"
    assert preview.related_memories[0].memory_id == first.memory_id
    assert preview.related_memories[0].scope_relation == "same"
    assert preview.related_memories[0].reviewed_verdict is Verdict.FALSE_POSITIVE
    assert preview.related_memories[0].conclusion


def generation_candidate(service, index, generation, *, environment="dev-corpus-eval"):
    run = _run(index)
    metadata = {"environment": environment, "experiment_id": "EXP-test"}
    if generation:
        patterns = SocMemoryPatternService(
            repository=service._candidate_repository,
            candidate_repository=service._candidate_repository,
            profile_registry=build_soc_memory_profile_registry(),
            accumulation_scope=MemoryPatternAccumulationScope(experiment_id="EXP-test", learning_generation=generation, event_start=datetime(2026, 1, 1, tzinfo=UTC), event_end=datetime(2027, 1, 1, tzinfo=UTC)),
        )
        observation = _observe(patterns, run, transport_ref=f"generation:{index}", environment=environment).observation
        metadata.update(memory_pattern_lineage_metadata(observation))
    command = memory_candidate_command_from_run_promotion(run, SocMemoryRunPromotionCommand(run_id=run.run_id, metadata=metadata), profile_registry=build_soc_memory_profile_registry())
    return service.propose_candidate(command, coordinate_learning=True)


@pytest.mark.parametrize("location", ["metadata", "source_metadata"])
def test_caller_metadata_cannot_forge_learning_generation(services, location):
    service, repository = services
    run = _run(741)
    command = memory_candidate_command_from_run_promotion(run, SocMemoryRunPromotionCommand(run_id=run.run_id, metadata={"environment": "dev-corpus-eval"}), profile_registry=build_soc_memory_profile_registry())
    (command.metadata if location == "metadata" else command.source.metadata).update(experiment_id="EXP-forged", learning_generation="ROUND-forged")
    with pytest.raises(SocServiceConflictError, match="学习代次|learning generation"):
        service.propose_candidate(command, coordinate_learning=True)
    assert not repository.list_memory_candidates()


@pytest.mark.parametrize("forgery", ["generation", "experiment", "run", "alert", "tenant"])
def test_learning_generation_must_match_exact_persisted_source(services, forgery):
    service, _ = services
    original = generation_candidate(service, 742, "ROUND-real")
    from soc_agent.contracts import SocMemoryCandidateCreateCommand

    command = SocMemoryCandidateCreateCommand(**{key: getattr(original, key) for key in SocMemoryCandidateCreateCommand.model_fields})
    if forgery in {"generation", "experiment"}:
        field = "learning_generation" if forgery == "generation" else "experiment_id"
        command.metadata[field] = "forged"
        command.source.metadata[field] = "forged"
    elif forgery == "tenant":
        command.tenant_id = command.tenant_scope = "other-tenant"
    else:
        setattr(command.source, forgery + "_id", "forged")
    with pytest.raises(SocServiceConflictError, match="学习代次|learning generation"):
        service.propose_candidate(command, coordinate_learning=True)


def test_publication_rechecks_generation_provenance(services):
    service, repository = services
    item = generation_candidate(service, 743, "ROUND-real")
    item.metadata["learning_generation"] = item.source.metadata["learning_generation"] = "ROUND-forged"
    repository.save_memory_candidate(item)
    with pytest.raises(SocServiceConflictError, match="学习代次|learning generation"):
        confirm(service, item, Verdict.FALSE_POSITIVE)
    assert repository.get_memory_record_by_candidate_id(item.candidate_id) is None


def test_explicit_revision_retains_its_validated_learning_generation(services):
    from soc_agent.contracts import SocMemoryRevisionCandidateCreateCommand, SocMemoryRevisionIssueType

    service, _ = services
    first = confirm(service, generation_candidate(service, 744, "ROUND-real"), Verdict.FALSE_POSITIVE)
    revision = service.propose_revision_candidate(
        SocMemoryRevisionCandidateCreateCommand(memory_id=first.memory_id, expected_record_version=first.version, issue_type=SocMemoryRevisionIssueType.INCORRECT_CONCLUSION, reason="模拟审核：该学习轮次中的经验需要纠正业务结论。"),
        context=_reviewer_context(key="generation-revision"),
    )
    assert revision.candidate.metadata["learning_generation"] == "ROUND-real"
    replacement = confirm(service, revision.candidate, Verdict.TRUE_POSITIVE)
    assert replacement.metadata["learning_generation"] == "ROUND-real"
    assert replacement.retrieval_enabled


@pytest.mark.parametrize("old_generation", [None, "ROUND-older"])
def test_fresh_dev_learning_can_review_and_publish_without_replacing_historical_memory(services, old_generation):
    service, repository = services
    first = confirm(service, generation_candidate(service, 701, old_generation), Verdict.FALSE_POSITIVE)
    second = generation_candidate(service, 702, "ROUND-new")
    preview = service.preview_candidate_governance(second.candidate_id, reviewer_verdict=Verdict.TRUE_POSITIVE)
    assert preview.recommendation == "new"
    assert not preview.related_memories
    new = confirm(service, second, Verdict.TRUE_POSITIVE)
    assert new.retrieval_enabled
    assert new.revision_lineage is None
    assert repository.get_memory_record(first.memory_id) == first


@pytest.mark.parametrize("environment,old_generation,new_generation", [("prd", "ROUND-old", "ROUND-new"), ("dev-corpus-eval", "ROUND-same", "ROUND-same")])
def test_generation_metadata_does_not_weaken_production_or_same_generation_publication(services, environment, old_generation, new_generation):
    service, _ = services
    confirm(service, generation_candidate(service, 711, old_generation, environment=environment), Verdict.FALSE_POSITIVE)
    second = generation_candidate(service, 712, new_generation, environment=environment)
    # A new proposal may coordinate to the approved same-generation record; create
    # a historical-style separate candidate to exercise the publication gate too.
    if second.status is SocMemoryCandidateStatus.CONFIRMED:
        second = second.model_copy(update={"candidate_id": "MC-explicit-second", "idempotency_key": "explicit-second", "status": SocMemoryCandidateStatus.PENDING_REVIEW})
        service._candidate_repository.save_memory_candidate(second)
    with pytest.raises(SocServiceConflictError, match="经验|Memory"):
        confirm(service, second, Verdict.TRUE_POSITIVE)


def test_generation_isolation_requires_an_experiment_and_exclusive_dev_environment(services):
    from soc_agent.memory.governance import same_learning_governance_scope

    service, _ = services
    ordinary = candidate(service, 719)
    scope = {**ordinary.applicability.required_facets, "environment": ["dev-corpus-eval", "prd"]}
    mixed = ordinary.model_copy(update={"applicability": ordinary.applicability.model_copy(update={"required_facets": scope}), "metadata": {"experiment_id": "EXP-test", "learning_generation": "ROUND-new"}})
    assert same_learning_governance_scope(ordinary, mixed)
    missing_experiment = mixed.model_copy(update={"applicability": mixed.applicability.model_copy(update={"required_facets": {**scope, "environment": ["dev-corpus-eval"]}}), "metadata": {"learning_generation": "ROUND-new"}})
    assert same_learning_governance_scope(ordinary, missing_experiment)


@pytest.mark.parametrize("same_generation", [False, True])
def test_dev_learning_retains_only_its_own_disabled_narrow_scope_boundaries(services, same_generation):
    from soc_agent.contracts import SocMemoryApplicabilityReport, SocMemoryApplicabilityStatus, SocMemoryMatch, SocMemoryQuery
    from soc_agent.memory.governance import prefer_specific_matches
    from soc_agent.memory.lessons import promote_memory_applicability_facets

    service, repository = services
    broad = confirm(service, generation_candidate(service, 721, "ROUND-new"), Verdict.FALSE_POSITIVE)
    narrow_spec = promote_memory_applicability_facets(broad.applicability, ["source_type"])
    metadata = {**broad.metadata, "learning_generation": "ROUND-new" if same_generation else "ROUND-old"}
    narrow = broad.model_copy(update={"memory_id": "MEM-disabled-narrow", "source_candidate_id": "MC-disabled-narrow", "applicability": narrow_spec, "retrieval_enabled": False, "metadata": metadata})
    repository.save_memory_record(narrow)
    match = SocMemoryMatch(
        memory_id=broad.memory_id,
        version=broad.version,
        record=broad,
        score=1,
        token_estimate=1,
        content_hash=broad.content_hash,
        facets_hash=broad.facets_hash,
        applicability_report=SocMemoryApplicabilityReport(status=SocMemoryApplicabilityStatus.APPLICABLE, policy_version="test"),
    )
    query = SocMemoryQuery(
        facets=broad.facets, metadata={"memory_profile_id": broad.applicability.profile_id, "memory_profile_version": broad.applicability.profile_version, "memory_feature_schema_version": broad.applicability.feature_schema_version}
    )
    result = prefer_specific_matches([match], build_soc_memory_profile_registry(), published_scopes=[broad, narrow], query=query)[0]
    assert (result.applicability_report.status is SocMemoryApplicabilityStatus.PARTIAL) is same_generation
    assert bool(service.scope_boundaries(broad.memory_id).exceptions) is same_generation


def test_stale_replacement_version_preserves_both_candidate_and_memory(services):
    service, repository = services
    first = confirm(service, candidate(service, 1), Verdict.FALSE_POSITIVE)
    second = candidate(service, 2, Verdict.TRUE_POSITIVE)
    with pytest.raises(SocServiceConflictError):
        confirm(service, second, Verdict.TRUE_POSITIVE, replaces_memory_id=first.memory_id, expected_replaced_version=first.version - 1)
    assert repository.get_memory_record(first.memory_id).retrieval_enabled
    assert repository.get_memory_candidate(second.candidate_id).status is SocMemoryCandidateStatus.PENDING_REVIEW


def test_nested_exception_survives_replacement_of_broad_answer(services):
    from soc_agent.memory.lessons import promote_memory_applicability_facets

    service, repository = services
    first = confirm(service, candidate(service, 1), Verdict.FALSE_POSITIVE)
    narrower = candidate(service, 2)
    key = next(iter(narrower.applicability.optional_facets))
    narrowed_spec = promote_memory_applicability_facets(narrower.applicability, [key])
    other = confirm(service, narrower, Verdict.FALSE_POSITIVE, record_applicability=narrowed_spec)
    challenge = candidate(service, 3, Verdict.TRUE_POSITIVE)
    replacement = confirm(service, challenge, Verdict.TRUE_POSITIVE, replaces_memory_id=first.memory_id, expected_replaced_version=first.version)
    assert not repository.get_memory_record(first.memory_id).retrieval_enabled
    assert repository.get_memory_record(other.memory_id).retrieval_enabled
    assert repository.get_memory_record_by_candidate_id(challenge.candidate_id).memory_id == replacement.memory_id


def test_model_suspicion_alone_is_not_a_reviewed_contradiction(services):
    service, _ = services
    confirm(service, candidate(service, 1), Verdict.FALSE_POSITIVE)
    challenge = candidate(service, 2, Verdict.SUSPICIOUS)
    result = service.preview_candidate_governance(challenge.candidate_id)
    assert result.recommendation == "inspect"
    assert result.related_memories[0].conclusion_relation == "undetermined"


def test_distinct_behavior_scopes_can_keep_opposite_conclusions(services):
    service, _ = services
    first = confirm(service, candidate(service, 1, network_protocol="tcp"), Verdict.FALSE_POSITIVE)
    second = candidate(service, 2, Verdict.TRUE_POSITIVE, network_protocol="udp")
    assert first.applicability.required_facets["behavior_fingerprint"] != second.applicability.required_facets["behavior_fingerprint"]
    result = confirm(service, second, Verdict.TRUE_POSITIVE)
    assert result.retrieval_enabled


def test_same_scope_agreement_supplements_instead_of_publishing_a_duplicate(services):
    service, _ = services
    first = confirm(service, candidate(service, 1), Verdict.FALSE_POSITIVE)
    second = candidate(service, 2)
    assert service.preview_candidate_governance(second.candidate_id, reviewer_verdict=Verdict.FALSE_POSITIVE).recommendation == "reinforce"
    with pytest.raises(SocServiceConflictError, match="补充"):
        confirm(service, second, Verdict.FALSE_POSITIVE)
    result = confirm(service, second, Verdict.FALSE_POSITIVE, replaces_memory_id=first.memory_id, expected_replaced_version=first.version)
    assert result.revision_lineage.issue_type.value == "lesson_incomplete"


def test_old_pending_candidates_are_compared_without_a_database_reset(services):
    service, repository = services
    first = candidate(service, 1)
    first.metadata.pop("governance_scope_key")
    repository.save_memory_candidate(first)
    assert candidate(service, 2, Verdict.TRUE_POSITIVE).candidate_id == first.candidate_id


def test_optional_entity_differences_do_not_prove_disjoint_scopes(services):
    from soc_agent.memory.governance import scope_relation

    service, _ = services
    spec = candidate(service, 1).applicability
    a = spec.model_copy(update={"required_facets": {**spec.required_facets, "entity": ["ip:10.1.1.1"]}})
    b = spec.model_copy(update={"required_facets": {**spec.required_facets, "entity": ["ip:10.2.2.2"]}})
    assert scope_relation(a, b, build_soc_memory_profile_registry()) == "overlap"
    b = b.model_copy(update={"excluded_facets": {"entity": ["ip:10.1.1.1"]}})
    assert scope_relation(a, b, build_soc_memory_profile_registry()) == "disjoint"


def test_concurrent_review_cannot_publish_opposing_legacy_candidates(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'concurrent.sqlite'}")
    create_soc_tables(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def service_for_thread():
        repository = SqlAlchemyAlertRepository(factory)
        return SocMemoryService(candidate_repository=repository, record_repository=repository, profile_registry=build_soc_memory_profile_registry())

    service = service_for_thread()
    first = candidate(service, 1)
    second = first.model_copy(update={"candidate_id": "MC-LEGACY-DUPLICATE", "idempotency_key": "old-distinct-candidate"}, deep=True)
    SqlAlchemyAlertRepository(factory).save_memory_candidate(second)
    barrier = Barrier(2)

    def publish(item, verdict):
        barrier.wait(timeout=5)
        try:
            confirm(service_for_thread(), item, verdict)
            return "published"
        except SocServiceConflictError:
            return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(publish, first, Verdict.FALSE_POSITIVE)
            b = pool.submit(publish, second, Verdict.TRUE_POSITIVE)
            assert sorted([a.result(timeout=15), b.result(timeout=15)]) == ["conflict", "published"]
        records = SqlAlchemyAlertRepository(factory).list_memory_records(retrieval_enabled=True)
        assert len(records) == 1
    finally:
        engine.dispose()


def test_audit_example_keeps_challenge_and_replacement_history(services, tmp_path):
    service, repository = services
    old = confirm(service, candidate(service, 1), Verdict.FALSE_POSITIVE)
    challenge = candidate(service, 2, Verdict.SUSPICIOUS)
    before = service.preview_candidate_governance(challenge.candidate_id, reviewer_verdict=Verdict.TRUE_POSITIVE)
    duplicate = candidate(service, 3, Verdict.TRUE_POSITIVE)
    assert duplicate.candidate_id == challenge.candidate_id
    with pytest.raises(SocServiceConflictError) as blocked:
        confirm(service, challenge, Verdict.TRUE_POSITIVE)
    successor = confirm(service, challenge, Verdict.TRUE_POSITIVE, replaces_memory_id=old.memory_id, expected_replaced_version=old.version)
    assert repository.get_memory_record(old.memory_id).status is SocMemoryRecordStatus.DEPRECATED
    directory = Path(os.environ.get("SOC_GOVERNANCE_REPORT_DIR", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    report = {
        "simulated": True,
        "real_llm_calls": 0,
        "purpose": "Synthetic governance state transitions, not model-quality or production truth.",
        "old_record_before": old.model_dump(mode="json"),
        "review_comparison": before.model_dump(mode="json"),
        "independent_publication_error": str(blocked.value),
        "same_scope_challenges_coalesced": duplicate.candidate_id == challenge.candidate_id,
        "old_record_after": repository.get_memory_record(old.memory_id).model_dump(mode="json"),
        "successor_record": successor.model_dump(mode="json"),
        "active_record_count": len(repository.list_memory_records(retrieval_enabled=True)),
    }
    (directory / "governance-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def test_draft_assistance_receives_database_comparison_without_writing(services):
    from soc_agent.core import SocMemoryLessonDraftService
    from soc_agent.prompts.memory_lesson import build_memory_lesson_draft_prompt

    service, repository = services
    old = confirm(service, candidate(service, 1), Verdict.FALSE_POSITIVE)
    second = candidate(service, 2, Verdict.TRUE_POSITIVE)
    captured = []

    class DraftSpy:
        def draft(self, item, *, reviewer_verdict, reviewer_context):
            captured.append(build_memory_lesson_draft_prompt(item, reviewer_verdict=reviewer_verdict, reviewer_context=reviewer_context))

    drafter = SocMemoryLessonDraftService(candidate_repository=repository, drafter=DraftSpy(), governance_service=service)
    drafter.draft_business_lesson(second.candidate_id, reviewer_verdict=Verdict.TRUE_POSITIVE, context=_reviewer_context(key="draft"))
    source = next(item for item in captured[0].source_catalog if item.label == "existing_memory_comparison")
    assert old.memory_id in source.value
    assert "false_positive" in source.value
    assert "tenant-policy handoff is not proof" in captured[0].system
    assert repository.get_memory_record(old.memory_id).version == old.version
    assert repository.get_memory_candidate(second.candidate_id).status is SocMemoryCandidateStatus.PENDING_REVIEW


def test_review_http_surface_forwards_replacement_and_returns_conflict(services):
    from fastapi import HTTPException
    from test_soc_memory_router import FakeRequest

    from app.gateway.routers import soc_memory

    service, _ = services
    old = confirm(service, candidate(service, 1), Verdict.FALSE_POSITIVE)
    second = candidate(service, 2, Verdict.TRUE_POSITIVE)
    request = FakeRequest(system_role="admin", idempotency_key="api-governance-test")
    payload = soc_memory.MemoryCandidateReviewRequest(
        decision="confirm",
        reason="Reviewer verified the new business facts for this exact scope.",
        record_lesson=_reviewed_business_lesson(),
        confirmed_verdict=Verdict.TRUE_POSITIVE,
        apply_to_future_matches=True,
        activate_retrieval=True,
        activation_valid_until=datetime.now(UTC) + timedelta(days=30),
        activation_review_after_days=7,
    )
    with pytest.raises(HTTPException) as conflict:
        soc_memory.review_memory_candidate(second.candidate_id, payload, request, service)
    assert conflict.value.status_code == 409
    payload.replaces_memory_id = old.memory_id
    payload.expected_replaced_version = old.version
    result = soc_memory.review_memory_candidate(second.candidate_id, payload, request, service)
    assert result.memory_record.revision_lineage.predecessor_memory_id == old.memory_id


def test_cli_confirmation_uses_the_same_replacement_boundary(services, monkeypatch, capsys):
    from soc_agent import cli

    service, repository = services
    old = confirm(service, candidate(service, 1), Verdict.FALSE_POSITIVE)
    challenge = candidate(service, 2, Verdict.TRUE_POSITIVE)
    monkeypatch.setattr(cli, "_repository_from_args", lambda _args: repository)
    args = cli._build_parser().parse_args(
        [
            "memory",
            "review",
            challenge.candidate_id,
            "--decision",
            "confirm",
            "--reason",
            "Reviewed synthetic replacement through CLI, preserving the audit chain.",
            "--confirmed-verdict",
            "true_positive",
            "--record-lesson-json",
            _reviewed_business_lesson().model_dump_json(),
            "--replaces-memory-id",
            old.memory_id,
            "--expected-replaced-version",
            str(old.version),
        ]
    )
    assert cli._memory_review(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["memory_record"]["revision_lineage"]["predecessor_memory_id"] == old.memory_id
    assert repository.get_memory_record(old.memory_id).status is SocMemoryRecordStatus.DEPRECATED
