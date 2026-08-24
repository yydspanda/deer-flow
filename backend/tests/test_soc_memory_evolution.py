from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AlertClassification,
    AlertSourceRef,
    AlertSourceType,
    AnalysisContextCatalogItem,
    AnalysisContextReferenceKind,
    AnalysisRun,
    AnalysisRunStatus,
    CorrectionCommand,
    Decision,
    DetectionRuleRef,
    EntrySurface,
    LLMAnalysisRequest,
    ServiceRequestContext,
    SocMemoryApplicabilityReport,
    SocMemoryApplicabilitySpec,
    SocMemoryApplicabilityStatus,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionDirective,
    SocMemoryDecisionEffect,
    SocMemoryDecisionImpact,
    SocMemoryRecord,
    SocMemoryReviewEffect,
    SocMemoryRevisionReviewCommand,
    SocMemoryRevisionReviewDecision,
    SocMemoryTargetArtifact,
    Verdict,
)
from soc_agent.core import (
    SocAutomationService,
    SocMemoryEvolutionService,
    SocReviewService,
)
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.utils.hashing import stable_hash


def _repository(path: Path) -> SqlAlchemyAlertRepository:
    engine = create_engine(f"sqlite:///{path}")
    create_soc_tables(engine)
    return SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))


def _analyst_context(key: str) -> ServiceRequestContext:
    return ServiceRequestContext(
        idempotency_key=key,
        actor=ActorContext(
            actor_id="analyst-001",
            actor_type=ActorType.USER,
            surface=EntrySurface.TEST,
            roles=["soc_analyst"],
        ),
    )


def _memory_reviewer_context(key: str) -> ServiceRequestContext:
    return ServiceRequestContext(
        idempotency_key=key,
        actor=ActorContext(
            actor_id="memory-reviewer-001",
            actor_type=ActorType.USER,
            surface=EntrySurface.TEST,
            roles=["soc_memory_reviewer"],
        ),
    )


def _active_benign_memory(now: datetime) -> SocMemoryRecord:
    facets = {
        "detection_key": ["pingan:ndr:reverse-shell"],
        "behavior_fingerprint": ["behavior:reverse-shell:v1"],
        "environment": ["prd"],
    }
    return SocMemoryRecord(
        memory_id="MEM-EVOLUTION-001",
        version=1,
        memory_type=SocMemoryCandidateType.BENIGN_PATTERN,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        tenant_scope="pingan",
        tenant_id="pingan",
        source_candidate_id="MC-EVOLUTION-001",
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.REPEATED_PATTERN,
            source_id="memory-pattern:reverse-shell",
        ),
        summary="Reviewed benign reverse-shell detector pattern",
        content="This exact detector class was confirmed as expected activity.",
        facets=facets,
        applicability=SocMemoryApplicabilitySpec(
            profile_id="pingan.soc",
            profile_version="2",
            feature_schema_version="pingan.soc.memory_features.v2",
            required_facets=facets,
            minimum_strong_anchor_matches=2,
        ),
        evidence_refs=["memory_pattern:cohort-001"],
        validity=SocMemoryCandidateValidity(
            valid_from=now - timedelta(days=1),
            valid_until=now + timedelta(days=90),
            review_after_days=30,
            notes="Reviewed operational cohort.",
        ),
        confidence=0.9,
        decision_impact=SocMemoryDecisionImpact.DETECTION_DECISION,
        decision_directive=SocMemoryDecisionDirective(
            effect=SocMemoryDecisionEffect.OVERRIDE,
            target_verdict=Verdict.FALSE_POSITIVE,
            review_effect=SocMemoryReviewEffect.CLEAR,
            minimum_match_score=5.0,
            required_facet_keys=[
                "detection_key",
                "behavior_fingerprint",
                "environment",
            ],
            rationale="Analyst-reviewed recurring benign class.",
        ),
        content_hash=f"sha256:{stable_hash('memory-content')}",
        facets_hash=f"sha256:{stable_hash(facets)}",
        retrieval_enabled=True,
        retrieval_policy_version="soc.memory_retrieval_activation_policy.v1",
        retrieval_valid_until=now + timedelta(days=30),
        retrieval_review_due_at=now + timedelta(days=7),
        retrieval_updated_by=ActorContext(
            actor_id="memory-reviewer",
            surface=EntrySurface.TEST,
        ),
        retrieval_updated_at=now,
        retrieval_reason="Approved for exact PingAn recurrence matches.",
        created_by=ActorContext(
            actor_id="memory-reviewer",
            surface=EntrySurface.TEST,
        ),
        created_at=now,
        updated_at=now,
    )


def _run(memory: SocMemoryRecord, now: datetime) -> AnalysisRun:
    applicability = SocMemoryApplicabilityReport(
        status=SocMemoryApplicabilityStatus.APPLICABLE,
        policy_version="soc.memory_applicability_policy.v1",
        profile_id="pingan.soc",
        profile_version="2",
        matched_required_facets={
            "detection_key": ["pingan:ndr:reverse-shell"],
            "behavior_fingerprint": ["behavior:reverse-shell:v1"],
            "environment": ["prd"],
        },
        matched_strong_anchor_count=2,
        reason_codes=["typed_applicability_satisfied"],
    )
    context_ref = "M-ABCDEF123456"
    request = LLMAnalysisRequest(
        alert_id="ALERT-EVOLUTION-001",
        tenant_id="pingan",
        environment="prd",
        source=AlertSourceRef(
            source_type=AlertSourceType.NIDS,
            source_system="zeus",
            integration_name="pingan_legacy_alert_platform",
        ),
        detection=DetectionRuleRef(
            detection_key="pingan:ndr:reverse-shell",
            rule_name="Reverse shell detector",
        ),
        classification=AlertClassification(category="command_and_control"),
        context_catalog=[
            AnalysisContextCatalogItem(
                context_ref=context_ref,
                kind=AnalysisContextReferenceKind.CONFIRMED_MEMORY,
                label=memory.summary,
                source_id=f"{memory.memory_id}@v{memory.version}",
                summary=memory.content,
                content_hash=stable_hash("projection"),
                metadata={
                    "memory_id": memory.memory_id,
                    "memory_version": memory.version,
                    "retrieval_score": 9.5,
                    "retrieval_policy_version": "soc.memory_retrieval_policy.v2",
                    "matched_facets": memory.facets,
                    "applicability_status": "applicable",
                    "applicability_report": applicability.model_dump(mode="json"),
                    "record_content_hash": memory.content_hash,
                    "record_facets_hash": memory.facets_hash,
                    "decision_directive_present": True,
                    "decision_directive_applicable": True,
                },
            )
        ],
    )
    return AnalysisRun(
        run_id="RUN-EVOLUTION-001",
        alert_id=request.alert_id,
        status=AnalysisRunStatus.NEEDS_REVIEW,
        started_at=now,
        ended_at=now,
        llm_analysis_request=request,
        decision=Decision(
            verdict=Verdict.SUSPICIOUS,
            confidence=0.72,
            suggested_action="review",
            needs_review=True,
            reason="Detector hit requires governed post-Runtime evaluation.",
        ),
    )


def test_memory_use_feedback_and_safety_suspension_are_persisted(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 6, 0, tzinfo=UTC)
    repository = _repository(tmp_path / "memory-evolution.db")
    memory = _active_benign_memory(now)
    run = _run(memory, now)
    repository.save_memory_record(memory)
    repository.save_run(run)

    automation = SocAutomationService(
        repository=repository,
        policy=None,
        environment="prd",
        memory_repository=repository,
        now_provider=lambda: now,
    )
    transition = automation.evaluate(
        run,
        context=_analyst_context("automation-evolution-001"),
    ).decision_transition
    assert transition.before.verdict is Verdict.SUSPICIOUS
    assert transition.after.verdict is Verdict.FALSE_POSITIVE

    evolution = SocMemoryEvolutionService(
        repository=repository,
        memory_record_repository=repository,
        automation_repository=repository,
        mutation_audit_repository=repository,
        mutation_uow=repository,
        now_provider=lambda: now,
    )
    uses = evolution.capture_run_usage(run)
    assert len(uses) == 1
    assert uses[0].effect.value == "overridden"

    corrected = SocReviewService(
        repository=repository,
        memory_record_repository=repository,
        mutation_audit_repository=repository,
        mutation_uow=repository,
    ).correct(
        CorrectionCommand(
            run_id=run.run_id,
            corrected_verdict=Verdict.TRUE_POSITIVE,
            reason=("Analyst confirmed real compromise; the prior benign pattern is not applicable to this occurrence."),
        ),
        context=_analyst_context("correction-evolution-001"),
    )

    correction = corrected.corrections[-1]
    assert correction.memory_use_ids == [uses[0].use_id]
    assert len(correction.memory_feedback_ids) == 1
    assert len(correction.memory_revision_proposal_ids) == 1
    assert correction.suspended_memory_ids == [memory.memory_id]
    assert correction.memory_candidate_id is None

    updated_memory = repository.get_memory_record(memory.memory_id)
    assert updated_memory is not None
    assert updated_memory.retrieval_enabled is False
    assert updated_memory.version == 2

    lineage = evolution.get_lineage(memory.memory_id)
    assert len(lineage.uses) == 1
    assert lineage.feedback[0].alignment.value == "contradicts"
    assert lineage.health[0].status.value == "suspended"
    assert lineage.health[0].use_count == 1
    assert lineage.health[0].contradiction_count == 1
    assert len(lineage.revision_proposals) == 1
    assert lineage.revision_proposals[0].proposed_target_verdict is Verdict.TRUE_POSITIVE

    review_command = SocMemoryRevisionReviewCommand(
        proposal_id=lineage.revision_proposals[0].proposal_id,
        decision=SocMemoryRevisionReviewDecision.ACCEPT,
        reason=("The contradiction is valid; keep the unsafe benign Memory suspended until a narrower replacement is reviewed."),
    )
    review_context = _memory_reviewer_context("memory-revision-review-001")
    reviewed = evolution.review_revision_proposal(
        review_command,
        context=review_context,
    )
    retried = evolution.review_revision_proposal(
        review_command,
        context=review_context,
    )

    assert reviewed.proposal.status.value == "accepted"
    assert reviewed.memory_record_changed is False
    assert reviewed.retrieval_reenabled is False
    assert retried.proposal == reviewed.proposal
    still_suspended = repository.get_memory_record(memory.memory_id)
    assert still_suspended is not None
    assert still_suspended.retrieval_enabled is False


def test_same_memory_version_has_one_final_use_effect_per_run(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 6, 30, tzinfo=UTC)
    repository = _repository(tmp_path / "memory-use-identity.db")
    memory = _active_benign_memory(now)
    run = _run(memory, now)
    assert run.llm_analysis_request is not None
    original = run.llm_analysis_request.context_catalog[0]
    duplicate_projection = original.model_copy(update={"context_ref": "M-DUPLICATE0001"})
    run = run.model_copy(
        update={
            "llm_analysis_request": run.llm_analysis_request.model_copy(
                update={
                    "context_catalog": [
                        duplicate_projection,
                        original,
                    ]
                }
            )
        }
    )
    repository.save_memory_record(memory)
    repository.save_run(run)

    transition = (
        SocAutomationService(
            repository=repository,
            policy=None,
            environment="prd",
            memory_repository=repository,
            now_provider=lambda: now,
        )
        .evaluate(
            run,
            context=_analyst_context("automation-memory-use-identity"),
        )
        .decision_transition
    )
    assert transition.after.verdict is Verdict.FALSE_POSITIVE
    assert len(transition.contributors) == 1

    evolution = SocMemoryEvolutionService(
        repository=repository,
        memory_record_repository=repository,
        automation_repository=repository,
        mutation_audit_repository=repository,
        mutation_uow=repository,
        now_provider=lambda: now,
    )
    first = evolution.capture_run_usage(run)
    replay = evolution.capture_run_usage(run)
    persisted = repository.list_memory_uses(run_id=run.run_id, limit=100)

    assert len(first) == 1
    assert len(replay) == 1
    assert len(persisted) == 1
    assert replay[0].use_id == first[0].use_id
    assert first[0].memory_id == memory.memory_id
    assert first[0].memory_version == memory.version
    assert first[0].effect.value == "overridden"
    assert first[0].directive_applied is True
    assert first[0].context_ref in {item.ref_id for item in transition.contributors}


def test_supporting_final_outcome_keeps_reviewed_memory_active(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 15, 7, 0, tzinfo=UTC)
    repository = _repository(tmp_path / "memory-support.db")
    memory = _active_benign_memory(now)
    run = _run(memory, now)
    repository.save_memory_record(memory)
    repository.save_run(run)

    SocAutomationService(
        repository=repository,
        policy=None,
        environment="prd",
        memory_repository=repository,
        now_provider=lambda: now,
    ).evaluate(
        run,
        context=_analyst_context("automation-support-001"),
    )
    SocMemoryEvolutionService(
        repository=repository,
        memory_record_repository=repository,
        automation_repository=repository,
        mutation_audit_repository=repository,
        mutation_uow=repository,
        now_provider=lambda: now,
    ).capture_run_usage(run)

    corrected = SocReviewService(
        repository=repository,
        memory_record_repository=repository,
        mutation_audit_repository=repository,
        mutation_uow=repository,
    ).correct(
        CorrectionCommand(
            run_id=run.run_id,
            corrected_verdict=Verdict.FALSE_POSITIVE,
            reason=("Analyst confirmed that this exact recurring detector class is the reviewed expected activity."),
        ),
        context=_analyst_context("correction-support-001"),
    )

    correction = corrected.corrections[-1]
    assert len(correction.memory_feedback_ids) == 1
    assert correction.memory_revision_proposal_ids == []
    assert correction.suspended_memory_ids == []
    current_memory = repository.get_memory_record(memory.memory_id)
    assert current_memory is not None
    assert current_memory.retrieval_enabled is True
    lineage = SocMemoryEvolutionService(
        repository=repository,
        memory_record_repository=repository,
        automation_repository=repository,
    ).get_lineage(memory.memory_id)
    assert lineage.feedback[0].alignment.value == "supports"
    assert lineage.health[0].status.value == "healthy"
    assert lineage.health[0].use_count == 1
    assert lineage.health[0].support_count == 1
