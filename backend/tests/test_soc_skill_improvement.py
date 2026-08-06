from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.cli import main
from soc_agent.contracts import (
    ActorContext,
    EntrySurface,
    ServiceRequestContext,
    SkillFeedbackObservationCreateCommand,
    SkillFeedbackSourceRef,
    SkillFeedbackSourceType,
    SkillImprovementCandidateStatus,
    SkillImprovementFailureFacet,
    SkillImprovementReviewCommand,
    SkillImprovementReviewDecision,
    SkillPackageVersionRef,
    SocEvaluationDataClass,
    SocMutationOperation,
)
from soc_agent.core import (
    SocServiceAuthorizationError,
    SocServiceConflictError,
    SocSkillImprovementService,
)
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.skill_improvement import InMemorySkillImprovementRepository

_PACKAGE_HASH = "5b4d67d365ee24b16b22c73f3bf8430cbefea80f0eef95cd007de1664f850431"
_GUIDANCE_HASH = "f840ccfc16ce9a799c7fa8065798df8f4d6453d781242c4573c8695590ddcf28"
_OBSERVED_AT = datetime(2026, 8, 5, 1, 0, tzinfo=UTC)


def _context(
    *,
    roles: list[str] | None = None,
    idempotency_key: str | None = None,
) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="skill-improvement-test",
            surface=EntrySurface.TEST,
            roles=roles or ["soc_engineer"],
        ),
        idempotency_key=idempotency_key,
    )


def _command(
    index: int,
    *,
    package_hash: str = _PACKAGE_HASH,
    scenario_key: str = "reverse_shell",
) -> SkillFeedbackObservationCreateCommand:
    return SkillFeedbackObservationCreateCommand(
        idempotency_key=f"pi03c-simulation-{package_hash[:8]}-{scenario_key}-{index}",
        tenant_id="tenant-simulation",
        data_class=SocEvaluationDataClass.SIMULATION,
        source=SkillFeedbackSourceRef(
            source_type=SkillFeedbackSourceType.SIMULATION_FIXTURE,
            source_id=f"SIM-FEEDBACK-{package_hash[:8]}-{scenario_key}-{index}",
            run_id=f"RUN-SIM-{index}",
            alert_id=f"ALERT-SIM-{index}",
            observed_at=_OBSERVED_AT + timedelta(minutes=index),
        ),
        target_skill=SkillPackageVersionRef(
            skill_name="soc-network-apt-triage",
            package_hash=package_hash,
            guidance_hash=_GUIDANCE_HASH,
        ),
        scenario_key=scenario_key,
        failure_facet=SkillImprovementFailureFacet.MANUAL_CHECK_GUIDANCE_INADEQUATE,
        feedback_summary="Repeated feedback says the manual check omits process ownership.",
        suggested_change="Add a bounded process-owner verification step.",
        representative_sample_ref=f"fixture://pi03c/reverse-shell/{index}",
        replay_set_refs=[f"fixture://pi03c/replay/{index}", "fixture://pi03c/negative/control"],
    )


def _seed_candidate(
    service: SocSkillImprovementService,
    *,
    package_hash: str = _PACKAGE_HASH,
) -> tuple[str, int]:
    result = None
    for index in range(1, 4):
        result = service.ingest_feedback(_command(index, package_hash=package_hash), context=_context())
    assert result is not None
    assert result.candidate is not None
    return result.candidate.candidate_id, result.candidate.version


def test_simulation_and_real_feedback_lanes_cannot_mix() -> None:
    payload = _command(1).model_dump(mode="json")
    payload["data_class"] = SocEvaluationDataClass.DESENSITIZED_REAL.value

    with pytest.raises(ValidationError, match="simulation feedback must use simulation_fixture"):
        SkillFeedbackObservationCreateCommand.model_validate(payload)


def test_distinct_sources_create_and_refresh_pending_candidate() -> None:
    repository = InMemorySkillImprovementRepository()
    service = SocSkillImprovementService(repository=repository)

    first = service.ingest_feedback(_command(1), context=_context())
    second = service.ingest_feedback(_command(2), context=_context())
    third = service.ingest_feedback(_command(3), context=_context())

    assert first.threshold_met is False
    assert second.threshold_met is False
    assert third.candidate_created is True
    assert third.candidate is not None
    assert third.candidate.status is SkillImprovementCandidateStatus.PENDING_REVIEW
    assert third.candidate.occurrence_count == 3
    assert third.candidate.mocked is True
    assert third.candidate.skill_mutation_allowed is False
    assert third.candidate.skill_activation_allowed is False
    assert third.candidate.memory_write_allowed is False
    assert third.candidate.real_quality_claim_allowed is False

    fourth = service.ingest_feedback(_command(4), context=_context())
    assert fourth.candidate_updated is True
    assert fourth.candidate is not None
    assert fourth.candidate.version == 2
    assert fourth.candidate.occurrence_count == 4

    retry = service.ingest_feedback(_command(4), context=_context())
    assert retry.idempotent is True
    assert retry.candidate is not None
    assert retry.candidate.version == 2
    assert len(service.list_candidates()) == 1


def test_typed_aggregation_key_does_not_merge_scenarios() -> None:
    service = SocSkillImprovementService(repository=InMemorySkillImprovementRepository())

    for index in range(1, 3):
        service.ingest_feedback(_command(index), context=_context())
    isolated = service.ingest_feedback(
        _command(3, scenario_key="webshell"),
        context=_context(),
    )

    assert isolated.distinct_source_count == 1
    assert isolated.candidate is None
    assert service.list_candidates() == []


def test_review_is_role_gated_audited_and_freezes_candidate() -> None:
    repository = InMemorySkillImprovementRepository()
    service = SocSkillImprovementService(repository=repository)
    candidate_id, version = _seed_candidate(service)
    command = SkillImprovementReviewCommand(
        candidate_id=candidate_id,
        decision=SkillImprovementReviewDecision.APPROVE_FOR_CHANGE,
        reason="Skill owner accepts this cohort for an isolated change and replay.",
        expected_version=version,
    )

    with pytest.raises(SocServiceAuthorizationError):
        service.review_candidate(command, context=_context(roles=["soc_analyst"]))

    review_context = _context(
        roles=["soc_skill_reviewer"],
        idempotency_key="pi03c-review-1",
    )
    reviewed = service.review_candidate(command, context=review_context)
    assert reviewed.candidate.status is SkillImprovementCandidateStatus.APPROVED_FOR_CHANGE
    assert reviewed.candidate.skill_mutation_allowed is False
    assert reviewed.candidate.skill_activation_allowed is False

    retry = service.review_candidate(command, context=review_context)
    assert retry.idempotent is True
    audits = repository.list_mutation_audits(
        operation=SocMutationOperation.SKILL_IMPROVEMENT_REVIEW,
        target_id=candidate_id,
    )
    assert len(audits) == 1

    frozen = service.ingest_feedback(_command(4), context=_context())
    assert frozen.candidate_frozen is True
    assert frozen.candidate is not None
    assert frozen.candidate.occurrence_count == 3
    replay = service.replay_candidate(candidate_id)
    assert replay.changed is True
    assert len(replay.diff.added_observation_ids) == 1
    assert replay.skill_behavior_replay_executed is False
    assert replay.skill_mutation_allowed is False


def test_supersession_requires_matching_lineage_and_never_activates_skill() -> None:
    repository = InMemorySkillImprovementRepository()
    service = SocSkillImprovementService(repository=repository)
    old_id, old_version = _seed_candidate(service)
    new_hash = "a" * 64
    new_id, _ = _seed_candidate(service, package_hash=new_hash)

    result = service.review_candidate(
        SkillImprovementReviewCommand(
            candidate_id=old_id,
            decision=SkillImprovementReviewDecision.SUPERSEDE,
            reason="A newer exact package version owns the follow-up cohort.",
            expected_version=old_version,
            superseded_by_candidate_id=new_id,
        ),
        context=_context(
            roles=["soc_skill_reviewer"],
            idempotency_key="pi03c-supersede-1",
        ),
    )

    assert result.candidate.status is SkillImprovementCandidateStatus.SUPERSEDED
    assert result.candidate.superseded_by_candidate_id == new_id
    assert result.candidate.skill_mutation_allowed is False


def test_sql_repository_persists_observations_candidate_and_review() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    service = SocSkillImprovementService(repository=repository)

    candidate_id, version = _seed_candidate(service)
    persisted = repository.get_skill_improvement_candidate(candidate_id)
    assert persisted is not None
    assert persisted.occurrence_count == 3
    assert len(repository.list_skill_feedback_observations()) == 3

    reviewed = service.review_candidate(
        SkillImprovementReviewCommand(
            candidate_id=candidate_id,
            decision=SkillImprovementReviewDecision.REJECT,
            reason="Simulation proves workflow reachability but not a real Skill defect.",
            expected_version=version,
        ),
        context=_context(
            roles=["soc_skill_reviewer"],
            idempotency_key="pi03c-sql-review-1",
        ),
    )
    assert reviewed.candidate.status is SkillImprovementCandidateStatus.REJECTED
    assert repository.get_skill_improvement_candidate(candidate_id) == reviewed.candidate


def test_idempotency_key_reuse_with_changed_content_is_rejected() -> None:
    service = SocSkillImprovementService(repository=InMemorySkillImprovementRepository())
    command = _command(1)
    service.ingest_feedback(command, context=_context())
    changed = command.model_copy(update={"suggested_change": "A different proposed change."})

    with pytest.raises(SocServiceConflictError, match="reused for different content"):
        service.ingest_feedback(changed, context=_context())


def test_cli_ingest_list_and_replay_simulation(tmp_path, capsys) -> None:
    database_url = f"sqlite:///{tmp_path / 'pi03c.db'}"
    fixture_path = tmp_path / "feedback.json"
    ingest_output = tmp_path / "reports" / "ingest.json"
    replay_output = tmp_path / "reports" / "replay.json"
    fixture_path.write_text(
        "[" + ",".join(_command(index).model_dump_json() for index in range(1, 4)) + "]",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "skill-improvement",
                "ingest",
                str(fixture_path),
                "--threshold",
                "3",
                "--init-db",
                "--database-url",
                database_url,
                "--output",
                str(ingest_output),
            ]
        )
        == 0
    )
    ingest_report = capsys.readouterr().out
    candidate_id = json.loads(ingest_report)["candidate_ids"][0]
    assert json.loads(ingest_output.read_text(encoding="utf-8")) == json.loads(ingest_report)

    assert (
        main(
            [
                "skill-improvement",
                "list",
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    listed = json.loads(capsys.readouterr().out)
    assert [item["candidate_id"] for item in listed] == [candidate_id]

    assert (
        main(
            [
                "skill-improvement",
                "replay",
                candidate_id,
                "--database-url",
                database_url,
                "--output",
                str(replay_output),
            ]
        )
        == 0
    )
    replay = json.loads(capsys.readouterr().out)
    assert json.loads(replay_output.read_text(encoding="utf-8")) == replay
    assert replay["changed"] is False
    assert replay["skill_behavior_replay_executed"] is False
