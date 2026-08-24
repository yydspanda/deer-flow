from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import (
    ActorAuthSource,
    ActorContext,
    ActorType,
    AlertClassification,
    AlertEntitySet,
    AlertSourceRef,
    AlertSourceType,
    AnalysisRun,
    AnalysisRunStatus,
    DetectionRuleRef,
    EntrySurface,
    HttpEntityRef,
    LLMAnalysisRequest,
    NetworkEntityRef,
    ServiceRequestContext,
    SocMemoryApplicabilityReport,
    SocMemoryApplicabilitySpec,
    SocMemoryApplicabilityStatus,
    SocMemoryBusinessLesson,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateStatus,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionImpact,
    SocMemoryRecordMatchTestCommand,
    SocMemoryRecordStatus,
    SocMemoryRetrievalActivationAction,
    SocMemoryRetrievalActivationCommand,
    SocMemoryRevisionCandidateCreateCommand,
    SocMemoryRevisionIssueType,
    SocMemoryTargetArtifact,
    SocMemoryUseEffect,
    SocMemoryUseRecord,
    SocMutationOperation,
    Verdict,
)
from soc_agent.core import SocMemoryService, SocServiceConflictError
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.memory import InMemoryMemoryCandidateRepository


class RevisionRepository(InMemoryMemoryCandidateRepository):
    def __init__(self) -> None:
        super().__init__()
        self._uses: list[SocMemoryUseRecord] = []
        self._runs: dict[str, AnalysisRun] = {}

    def save_run(self, run: AnalysisRun) -> None:
        self._runs[run.run_id] = run

    def get_run(self, run_id: str) -> AnalysisRun | None:
        return self._runs.get(run_id)

    def list_runs_by_alert_id(
        self,
        alert_id: str,
        *,
        limit: int = 20,
    ) -> list[AnalysisRun]:
        return [run for run in self._runs.values() if run.alert_id == alert_id][:limit]

    def save_memory_use(self, record: SocMemoryUseRecord) -> None:
        self._uses.append(record)

    def list_memory_uses(
        self,
        *,
        memory_id: str | None = None,
        run_id: str | None = None,
        alert_id: str | None = None,
        limit: int = 500,
    ) -> list[SocMemoryUseRecord]:
        records = self._uses
        if memory_id is not None:
            records = [item for item in records if item.memory_id == memory_id]
        if run_id is not None:
            records = [item for item in records if item.run_id == run_id]
        if alert_id is not None:
            records = [item for item in records if item.alert_id == alert_id]
        return records[:limit]


def _reviewer_context(*, key: str) -> ServiceRequestContext:
    return ServiceRequestContext(
        idempotency_key=key,
        actor=ActorContext(
            actor_id="memory-reviewer-001",
            actor_type=ActorType.USER,
            surface=EntrySurface.WEB,
            roles=["soc_memory_reviewer"],
            auth_source=ActorAuthSource.SESSION,
        ),
    )


def _lesson(conclusion: str) -> SocMemoryBusinessLesson:
    return SocMemoryBusinessLesson(
        conclusion=conclusion,
        business_rationale=["运营人员已经结合当前告警和业务背景完成确认。"],
        applicability_conditions=["仅适用于相同检测规则和强行为指纹。"],
        generalization_boundaries=["不同业务行为或不同关键行为指纹不能直接复用。"],
        invalidation_conditions=["出现新的高可信攻击证据时立即停止复用。"],
        handling_guidance=["命中精确范围时复用结论，并保留完整决策留痕。"],
    )


def _source_run_for_revision() -> AnalysisRun:
    return AnalysisRun(
        run_id="RUN-WRONG-MEMORY",
        alert_id="ALERT-WRONG-MEMORY",
        status=AnalysisRunStatus.SUCCESS,
        input_payload={"alert_id": "ALERT-WRONG-MEMORY"},
        input_hash="a" * 64,
        llm_analysis_request=LLMAnalysisRequest(
            alert_id="ALERT-WRONG-MEMORY",
            tenant_id="pingan",
            environment="dev",
            source=AlertSourceRef(
                source_type=AlertSourceType.NIDS,
                source_system="sec_guard_apt",
                vendor="pingan",
                product="ndr",
                integration_name="pingan_legacy_alert_platform",
            ),
            detection=DetectionRuleRef(
                rule_code="RPAADM_000558",
                rule_name="红队IP监控",
                detection_key="sec_guard_apt:rule_code:rpaadm_000558",
            ),
            classification=AlertClassification(
                category="web_attack",
                severity="high",
                technique=["T1190"],
            ),
            canonical_entities=AlertEntitySet(
                network=NetworkEntityRef(
                    source_ip="36.32.3.213",
                    destination_ip="124.196.50.91",
                    src_port=38117,
                    dst_port=80,
                    protocol="http",
                ),
                http=HttpEntityRef(
                    method="GET",
                    host="service.example.internal",
                    path="/health",
                    port=80,
                    protocol="http",
                ),
            ),
        ),
    )


def _candidate_command(now: datetime) -> SocMemoryCandidateCreateCommand:
    facets = {
        "detection_key": ["pingan:ndr:reverse-shell"],
        "behavior_fingerprint": ["behavior:reverse-shell:v1"],
        "environment": ["dev"],
    }
    return SocMemoryCandidateCreateCommand(
        candidate_type=SocMemoryCandidateType.BENIGN_PATTERN,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary="内部 LLM 服务访问触发反弹 Shell 检测",
        content="相同规则和行为指纹对应内部 askbob-gpt 服务调用。",
        tenant_scope="pingan",
        tenant_id="pingan",
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.REPEATED_PATTERN,
            source_id="memory-pattern:reverse-shell",
            run_id="RUN-CONSTRUCTION",
            alert_id="ALERT-CONSTRUCTION",
        ),
        evidence_refs=["memory_pattern:cohort-reverse-shell"],
        validity=SocMemoryCandidateValidity(
            valid_from=now,
            valid_until=now + timedelta(days=90),
            review_after_days=30,
            notes="Reviewed recurring PingAn detector pattern.",
        ),
        confidence=0.9,
        decision_impact=SocMemoryDecisionImpact.DETECTION_DECISION,
        facets=facets,
        applicability=SocMemoryApplicabilitySpec(
            profile_id="pingan.soc",
            profile_version="5",
            feature_schema_version="pingan.soc.memory_features.v4",
            required_facets={
                "detection_key": facets["detection_key"],
                "behavior_fingerprint": facets["behavior_fingerprint"],
            },
            optional_facets={"environment": facets["environment"]},
            minimum_optional_matches=0,
            minimum_strong_anchor_matches=2,
        ),
        idempotency_key="candidate:reverse-shell",
        metadata={
            "memory_profile_id": "pingan.soc",
            "memory_profile_version": "5",
            "memory_feature_schema_version": "pingan.soc.memory_features.v4",
            "lineage_key": "ML-PINGAN-REVERSE-SHELL",
        },
    )


def _active_memory_fixture(
    repository: RevisionRepository | SqlAlchemyAlertRepository,
    *,
    now: datetime,
) -> tuple[SocMemoryService, str, str]:
    source_run = _source_run_for_revision()
    repository.save_run(source_run)
    service = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
        memory_evolution_repository=repository,
        mutation_audit_repository=repository,
        analysis_run_repository=repository,
        profile_registry=build_soc_memory_profile_registry(),
        now_provider=lambda: now,
    )
    candidate = service.propose_candidate(_candidate_command(now))
    confirmed = service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="运营专家确认该模式可以作为已知误报经验复用。",
            record_lesson=_lesson("该访问是内部 askbob-gpt 服务调用，并非真实反弹 Shell。"),
            confirmed_verdict=Verdict.FALSE_POSITIVE,
            apply_to_future_matches=True,
        ),
        context=_reviewer_context(key="memory-review:initial"),
    )
    assert confirmed.memory_record is not None
    active = service.set_retrieval_activation(
        SocMemoryRetrievalActivationCommand(
            memory_id=confirmed.memory_record.memory_id,
            action=SocMemoryRetrievalActivationAction.ENABLE,
            expected_record_version=confirmed.memory_record.version,
            reason="启用已审核的精确匹配经验。",
            activation_valid_until=now + timedelta(days=60),
            review_after_days=30,
        ),
        context=_reviewer_context(key="memory-retrieval:initial"),
    ).record
    repository.save_memory_use(
        SocMemoryUseRecord(
            idempotency_key="memory-use:revision-source",
            memory_id=active.memory_id,
            memory_version=active.version,
            memory_content_hash=active.content_hash,
            memory_facets_hash=active.facets_hash,
            run_id="RUN-WRONG-MEMORY",
            alert_id="ALERT-WRONG-MEMORY",
            tenant_id="pingan",
            context_ref="M-001",
            retrieval_policy_version="soc.memory_retrieval_policy.v2",
            retrieval_score=12.0,
            matched_facets=active.facets,
            applicability_report=SocMemoryApplicabilityReport(
                status=SocMemoryApplicabilityStatus.APPLICABLE,
                policy_version="soc.memory_applicability_policy.v1",
                profile_id="pingan.soc",
                profile_version="5",
                matched_required_facets=active.applicability.required_facets,
                matched_optional_facets={"environment": ["dev"]},
                matched_strong_anchor_count=2,
            ),
            base_verdict=Verdict.SUSPICIOUS,
            effective_verdict=Verdict.FALSE_POSITIVE,
            effect=SocMemoryUseEffect.OVERRIDDEN,
            directive_applied=True,
            created_at=now,
        )
    )
    return service, candidate.candidate_id, active.memory_id


def test_applicability_revision_reprojects_scope_from_exact_source_run() -> None:
    now = datetime(2026, 8, 21, 8, 30, tzinfo=UTC)
    repository = RevisionRepository()
    service, _, memory_id = _active_memory_fixture(repository, now=now)
    predecessor = service.get_record(memory_id)

    result = service.propose_revision_candidate(
        SocMemoryRevisionCandidateCreateCommand(
            memory_id=memory_id,
            expected_record_version=predecessor.version,
            source_run_id="RUN-WRONG-MEMORY",
            issue_type=SocMemoryRevisionIssueType.APPLICABILITY_TOO_BROAD,
            reason="旧经验只按规则命中，必须用当前误命中运行重新生成精确行为范围。",
        ),
        context=_reviewer_context(key="memory-revision:create:reproject-scope"),
    )

    candidate = result.candidate
    assert candidate.applicability is not None
    assert candidate.applicability.profile_id == "pingan.soc"
    assert candidate.applicability.profile_version == "6"
    assert candidate.applicability.required_facets["environment"] == ["dev"]
    assert candidate.applicability.required_facets["detection_key"] == ["sec_guard_apt:rule_code:rpaadm_000558"]
    assert candidate.applicability.required_facets["behavior_fingerprint"] == candidate.facets["behavior_fingerprint"]
    assert candidate.decision_impact is SocMemoryDecisionImpact.DETECTION_DECISION
    assert candidate.metadata["revision_scope_source"] == "source_run_profile_projection"
    assert candidate.metadata["memory_profile_version"] == "6"
    assert candidate.facets != predecessor.facets


def test_manual_revision_suspends_old_memory_and_creates_review_candidate() -> None:
    now = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
    repository = RevisionRepository()
    service, old_candidate_id, memory_id = _active_memory_fixture(
        repository,
        now=now,
    )
    old_record = service.get_record(memory_id)
    command = SocMemoryRevisionCandidateCreateCommand(
        memory_id=memory_id,
        expected_record_version=old_record.version,
        source_run_id="RUN-WRONG-MEMORY",
        issue_type=SocMemoryRevisionIssueType.INCORRECT_CONCLUSION,
        reason="当前告警出现了新的高可信攻击证据，旧的误报结论不应继续复用。",
    )
    context = _reviewer_context(key="memory-revision:create:001")

    result = service.propose_revision_candidate(command, context=context)
    retried = service.propose_revision_candidate(command, context=context)

    assert retried == result
    assert result.previous_retrieval_enabled is True
    assert result.predecessor_record.retrieval_enabled is False
    assert result.predecessor_record.version == old_record.version + 1
    assert result.candidate.status is SocMemoryCandidateStatus.PENDING_REVIEW
    assert result.candidate.revision_lineage is not None
    assert result.candidate.revision_lineage.predecessor_memory_id == memory_id
    assert result.candidate.revision_lineage.predecessor_memory_version == old_record.version
    assert result.candidate.revision_lineage.suspended_record_version == result.predecessor_record.version
    assert result.candidate.revision_lineage.source_memory_use_id is not None
    assert result.candidate.source.run_id == "RUN-WRONG-MEMORY"
    assert result.candidate.source.alert_id == "ALERT-WRONG-MEMORY"
    assert result.candidate.source.source_type is SocMemoryCandidateSourceType.MEMORY_REVISION
    assert "旧版 Business Lesson" in result.candidate.content
    assert service.get_candidate(old_candidate_id).status is SocMemoryCandidateStatus.CONFIRMED


def test_operator_can_propose_revision_without_a_later_memory_use() -> None:
    """The Memory inventory is also a valid governed correction surface."""

    now = datetime(2026, 8, 21, 9, 30, tzinfo=UTC)
    repository = RevisionRepository()
    service, _, memory_id = _active_memory_fixture(repository, now=now)
    predecessor = service.get_record(memory_id)

    result = service.propose_revision_candidate(
        SocMemoryRevisionCandidateCreateCommand(
            memory_id=memory_id,
            expected_record_version=predecessor.version,
            issue_type=SocMemoryRevisionIssueType.LESSON_INCOMPLETE,
            reason="运营人员在 Memory 台账中发现 Business Lesson 缺少适用边界，需要直接发起版本化修订。",
        ),
        context=_reviewer_context(key="memory-revision:create:operator-direct"),
    )

    assert result.predecessor_record.retrieval_enabled is False
    assert result.candidate.revision_lineage is not None
    assert result.candidate.revision_lineage.revision_origin == "operator_direct"
    assert result.candidate.revision_lineage.source_memory_use_id is None
    assert result.candidate.revision_lineage.source_run_id == predecessor.source.run_id
    assert result.candidate.revision_lineage.source_alert_id == predecessor.source.alert_id
    assert result.candidate.metadata["revision_origin"] == "operator_direct"


def test_operator_can_diagnose_one_memory_against_a_persisted_alert_run() -> None:
    now = datetime(2026, 8, 21, 9, 45, tzinfo=UTC)
    repository = RevisionRepository()
    service, _, memory_id = _active_memory_fixture(repository, now=now)

    result = service.test_record_match(
        SocMemoryRecordMatchTestCommand(
            memory_id=memory_id,
            alert_id="ALERT-WRONG-MEMORY",
        )
    )

    assert result.record.memory_id == memory_id
    assert result.run_id == "RUN-WRONG-MEMORY"
    assert result.alert_id == "ALERT-WRONG-MEMORY"
    assert result.profile_id == "pingan.soc"
    assert result.retrieval.query.metadata["target_memory_id"] == memory_id
    assert result.matched is False
    assert "missing_strong_anchor" in result.exclusion_reasons


def test_confirmed_revision_supersedes_old_memory_without_overwriting_history() -> None:
    now = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)
    repository = RevisionRepository()
    service, old_candidate_id, memory_id = _active_memory_fixture(
        repository,
        now=now,
    )
    old_record = service.get_record(memory_id)
    revision = service.propose_revision_candidate(
        SocMemoryRevisionCandidateCreateCommand(
            memory_id=memory_id,
            expected_record_version=old_record.version,
            source_run_id="RUN-WRONG-MEMORY",
            issue_type=SocMemoryRevisionIssueType.INCORRECT_CONCLUSION,
            reason="新的人工复核确认该行为在当前条件下是真实风险，需要替换旧经验。",
        ),
        context=_reviewer_context(key="memory-revision:create:002"),
    )
    confirmed = service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=revision.candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="确认修订后的风险经验，并替代旧版误报经验。",
            record_lesson=_lesson("该行为在当前精确匹配条件下代表真实反弹 Shell 风险。"),
            confirmed_verdict=Verdict.TRUE_POSITIVE,
            apply_to_future_matches=True,
            activate_retrieval=True,
            activation_valid_until=now + timedelta(days=45),
            activation_review_after_days=15,
        ),
        context=_reviewer_context(key="memory-revision:confirm:002"),
    )

    assert confirmed.memory_record is not None
    replacement = confirmed.memory_record
    predecessor = service.get_record(memory_id)
    old_candidate = service.get_candidate(old_candidate_id)
    assert replacement.memory_id != predecessor.memory_id
    assert replacement.status is SocMemoryRecordStatus.CONFIRMED
    assert replacement.retrieval_enabled is True
    assert replacement.revision_lineage == revision.candidate.revision_lineage
    assert predecessor.status is SocMemoryRecordStatus.DEPRECATED
    assert predecessor.retrieval_enabled is False
    assert predecessor.superseded_by_memory_id == replacement.memory_id
    assert predecessor.supersession_reason == "确认修订后的风险经验，并替代旧版误报经验。"
    assert old_candidate.status is SocMemoryCandidateStatus.SUPERSEDED
    assert old_candidate.superseded_by_candidate_id == revision.candidate.candidate_id
    assert service.get_candidate(revision.candidate.candidate_id).status is SocMemoryCandidateStatus.CONFIRMED


def test_revision_requires_the_source_run_to_have_used_the_memory() -> None:
    now = datetime(2026, 8, 21, 11, 0, tzinfo=UTC)
    repository = RevisionRepository()
    service, _, memory_id = _active_memory_fixture(repository, now=now)
    record = service.get_record(memory_id)

    with pytest.raises(
        SocServiceConflictError,
        match="did not use memory",
    ):
        service.propose_revision_candidate(
            SocMemoryRevisionCandidateCreateCommand(
                memory_id=memory_id,
                expected_record_version=record.version,
                source_run_id="RUN-UNRELATED",
                issue_type=SocMemoryRevisionIssueType.APPLICABILITY_TOO_BROAD,
                reason="该运行未实际命中这条 Memory，不能作为修订依据。",
            ),
            context=_reviewer_context(key="memory-revision:create:unrelated"),
        )

    assert service.get_record(memory_id).retrieval_enabled is True


def test_revision_cas_marks_an_already_disabled_memory_as_pending() -> None:
    now = datetime(2026, 8, 21, 11, 30, tzinfo=UTC)
    repository = RevisionRepository()
    service, _, memory_id = _active_memory_fixture(repository, now=now)
    active = service.get_record(memory_id)
    disabled = service.set_retrieval_activation(
        SocMemoryRetrievalActivationCommand(
            memory_id=memory_id,
            action=SocMemoryRetrievalActivationAction.DISABLE,
            expected_record_version=active.version,
            reason="运营人员先暂停该 Memory，准备核对异常命中。",
        ),
        context=_reviewer_context(key="memory-retrieval:pre-revision-disable"),
    ).record

    result = service.propose_revision_candidate(
        SocMemoryRevisionCandidateCreateCommand(
            memory_id=memory_id,
            expected_record_version=disabled.version,
            source_run_id="RUN-WRONG-MEMORY",
            issue_type=SocMemoryRevisionIssueType.APPLICABILITY_TOO_BROAD,
            reason="该 Memory 已暂停，但仍需冻结版本并进入正式范围修订审核。",
        ),
        context=_reviewer_context(key="memory-revision:create:disabled"),
    )

    assert result.previous_retrieval_enabled is False
    assert result.predecessor_record.version == disabled.version + 1
    assert result.predecessor_record.retrieval_enabled is False
    assert result.predecessor_record.metadata["revision_pending"] is True


def test_pending_revision_blocks_predecessor_retrieval_reactivation() -> None:
    now = datetime(2026, 8, 21, 11, 40, tzinfo=UTC)
    repository = RevisionRepository()
    service, _, memory_id = _active_memory_fixture(repository, now=now)
    active = service.get_record(memory_id)
    revision = service.propose_revision_candidate(
        SocMemoryRevisionCandidateCreateCommand(
            memory_id=memory_id,
            expected_record_version=active.version,
            source_run_id="RUN-WRONG-MEMORY",
            issue_type=SocMemoryRevisionIssueType.INCORRECT_CONCLUSION,
            reason="当前运行证明旧结论有误，修订审核完成前不得重新参与召回。",
        ),
        context=_reviewer_context(key="memory-revision:create:block-enable"),
    )

    with pytest.raises(
        SocServiceConflictError,
        match="pending revision",
    ):
        service.set_retrieval_activation(
            SocMemoryRetrievalActivationCommand(
                memory_id=memory_id,
                action=SocMemoryRetrievalActivationAction.ENABLE,
                expected_record_version=revision.predecessor_record.version,
                reason="尝试在修订待审期间重新启用旧经验。",
                activation_valid_until=now + timedelta(days=30),
                review_after_days=15,
            ),
            context=_reviewer_context(key="memory-retrieval:blocked-by-revision"),
        )

    assert service.get_record(memory_id) == revision.predecessor_record


def test_memory_allows_only_one_pending_revision() -> None:
    now = datetime(2026, 8, 21, 11, 45, tzinfo=UTC)
    repository = RevisionRepository()
    service, _, memory_id = _active_memory_fixture(repository, now=now)
    active = service.get_record(memory_id)
    first = service.propose_revision_candidate(
        SocMemoryRevisionCandidateCreateCommand(
            memory_id=memory_id,
            expected_record_version=active.version,
            source_run_id="RUN-WRONG-MEMORY",
            issue_type=SocMemoryRevisionIssueType.INCORRECT_CONCLUSION,
            reason="第一个修订已经暂停旧经验并进入审核，必须先完成该治理流程。",
        ),
        context=_reviewer_context(key="memory-revision:create:first-open"),
    )

    with pytest.raises(
        SocServiceConflictError,
        match="already has a pending revision",
    ):
        service.propose_revision_candidate(
            SocMemoryRevisionCandidateCreateCommand(
                memory_id=memory_id,
                expected_record_version=first.predecessor_record.version,
                source_run_id="RUN-WRONG-MEMORY",
                issue_type=SocMemoryRevisionIssueType.LESSON_INCOMPLETE,
                reason="尝试在第一个修订尚未结束时再创建一个并行修订候选。",
            ),
            context=_reviewer_context(key="memory-revision:create:second-open"),
        )

    assert service.get_record(memory_id) == first.predecessor_record


def test_rejected_revision_closes_freeze_but_requires_explicit_reactivation() -> None:
    now = datetime(2026, 8, 21, 11, 50, tzinfo=UTC)
    repository = RevisionRepository()
    service, _, memory_id = _active_memory_fixture(repository, now=now)
    active = service.get_record(memory_id)
    revision = service.propose_revision_candidate(
        SocMemoryRevisionCandidateCreateCommand(
            memory_id=memory_id,
            expected_record_version=active.version,
            source_run_id="RUN-WRONG-MEMORY",
            issue_type=SocMemoryRevisionIssueType.APPLICABILITY_TOO_BROAD,
            reason="怀疑旧经验适用范围过宽，先暂停召回并交给运营人员审核。",
        ),
        context=_reviewer_context(key="memory-revision:create:reject"),
    )

    rejected = service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=revision.candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.REJECT,
            reason="复核后未形成可靠的新经验，但旧经验仍需保持停用并由人工决定是否恢复。",
        ),
        context=_reviewer_context(key="memory-revision:reject"),
    )

    predecessor = service.get_record(memory_id)
    assert rejected.candidate.status is SocMemoryCandidateStatus.REJECTED
    assert predecessor.version == revision.predecessor_record.version + 1
    assert predecessor.retrieval_enabled is False
    assert predecessor.metadata["revision_pending"] is False
    assert predecessor.metadata["revision_resolution"] == "rejected"

    reactivated = service.set_retrieval_activation(
        SocMemoryRetrievalActivationCommand(
            memory_id=memory_id,
            action=SocMemoryRetrievalActivationAction.ENABLE,
            expected_record_version=predecessor.version,
            reason="运营人员复核原经验后，明确决定恢复其精确匹配召回。",
            activation_valid_until=now + timedelta(days=30),
            review_after_days=15,
        ),
        context=_reviewer_context(key="memory-retrieval:after-rejected-revision"),
    ).record
    assert reactivated.retrieval_enabled is True


def test_rejected_revision_cannot_be_reopened_with_stale_lineage() -> None:
    now = datetime(2026, 8, 21, 11, 55, tzinfo=UTC)
    repository = RevisionRepository()
    service, _, memory_id = _active_memory_fixture(repository, now=now)
    active = service.get_record(memory_id)
    revision = service.propose_revision_candidate(
        SocMemoryRevisionCandidateCreateCommand(
            memory_id=memory_id,
            expected_record_version=active.version,
            source_run_id="RUN-WRONG-MEMORY",
            issue_type=SocMemoryRevisionIssueType.LESSON_INCOMPLETE,
            reason="旧 Business Lesson 遗漏了关键边界，需要进入受治理修订流程。",
        ),
        context=_reviewer_context(key="memory-revision:create:no-reopen"),
    )
    service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=revision.candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.REJECT,
            reason="本次修订材料不足，结束当前修订并保留旧经验停用状态。",
        ),
        context=_reviewer_context(key="memory-revision:reject:no-reopen"),
    )

    with pytest.raises(
        SocServiceConflictError,
        match="cannot be reopened",
    ):
        service.review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=revision.candidate.candidate_id,
                decision=SocMemoryCandidateReviewDecision.REOPEN,
                reason="尝试重新打开已经结束且版本已变化的修订候选。",
            ),
            context=_reviewer_context(key="memory-revision:reopen:blocked"),
        )


def test_revision_replacement_is_atomic_and_durable_in_sqlite(tmp_path: Path) -> None:
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    engine = create_engine(f"sqlite:///{tmp_path / 'memory-revision.sqlite'}")
    create_soc_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlAlchemyAlertRepository(session_factory)
    service, old_candidate_id, memory_id = _active_memory_fixture(
        repository,
        now=now,
    )
    old_record = service.get_record(memory_id)

    revision = service.propose_revision_candidate(
        SocMemoryRevisionCandidateCreateCommand(
            memory_id=memory_id,
            expected_record_version=old_record.version,
            source_run_id="RUN-WRONG-MEMORY",
            issue_type=SocMemoryRevisionIssueType.LESSON_INCOMPLETE,
            reason="旧经验遗漏了关键业务边界，需要暂停召回并补充完整后再启用。",
        ),
        context=_reviewer_context(key="memory-revision:sqlite:create"),
    )

    reloaded_repository = SqlAlchemyAlertRepository(session_factory)
    reloaded_service = SocMemoryService(
        candidate_repository=reloaded_repository,
        record_repository=reloaded_repository,
        memory_evolution_repository=reloaded_repository,
        mutation_audit_repository=reloaded_repository,
        now_provider=lambda: now,
    )
    persisted_candidate = reloaded_service.get_candidate(revision.candidate.candidate_id)
    persisted_predecessor = reloaded_service.get_record(memory_id)
    assert persisted_candidate.revision_lineage is not None
    assert persisted_predecessor.retrieval_enabled is False

    confirmed = reloaded_service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=persisted_candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason="审核人补齐 Business Lesson 后确认替换旧经验。",
            record_lesson=_lesson("补齐边界后的经验可以在精确匹配条件下复用。"),
            confirmed_verdict=Verdict.FALSE_POSITIVE,
            apply_to_future_matches=True,
            activate_retrieval=True,
            activation_valid_until=now + timedelta(days=45),
            activation_review_after_days=15,
        ),
        context=_reviewer_context(key="memory-revision:sqlite:confirm"),
    )

    assert confirmed.memory_record is not None
    final_repository = SqlAlchemyAlertRepository(session_factory)
    final_reader = SocMemoryService(
        candidate_repository=final_repository,
        record_repository=final_repository,
    )
    predecessor = final_reader.get_record(memory_id)
    successor = final_reader.get_record(confirmed.memory_record.memory_id)
    assert predecessor.status is SocMemoryRecordStatus.DEPRECATED
    assert predecessor.superseded_by_memory_id == successor.memory_id
    assert final_reader.get_candidate(old_candidate_id).status is (SocMemoryCandidateStatus.SUPERSEDED)
    assert successor.retrieval_enabled is True
    assert successor.revision_lineage == persisted_candidate.revision_lineage


@pytest.mark.parametrize("fail_after_write", [1, 2, 3])
def test_revision_creation_rolls_back_every_write_on_failure(
    tmp_path: Path,
    fail_after_write: int,
) -> None:
    now = datetime(2026, 8, 21, 13, 0, tzinfo=UTC)
    engine = create_engine(f"sqlite:///{tmp_path / f'memory-revision-failure-{fail_after_write}.sqlite'}")
    create_soc_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed_repository = SqlAlchemyAlertRepository(session_factory)
    _, _, memory_id = _active_memory_fixture(seed_repository, now=now)
    original = SocMemoryService(
        candidate_repository=seed_repository,
        record_repository=seed_repository,
    ).get_record(memory_id)

    def fail_on_write(write_count: int) -> None:
        if write_count == fail_after_write:
            raise RuntimeError(f"injected revision failure after write {write_count}")

    failing_repository = SqlAlchemyAlertRepository(
        session_factory,
        mutation_write_hook=fail_on_write,
    )
    failing_service = SocMemoryService(
        candidate_repository=failing_repository,
        record_repository=failing_repository,
        memory_evolution_repository=failing_repository,
        mutation_audit_repository=failing_repository,
        now_provider=lambda: now,
    )

    with pytest.raises(
        RuntimeError,
        match=f"injected revision failure after write {fail_after_write}",
    ):
        failing_service.propose_revision_candidate(
            SocMemoryRevisionCandidateCreateCommand(
                memory_id=memory_id,
                expected_record_version=original.version,
                source_run_id="RUN-WRONG-MEMORY",
                issue_type=SocMemoryRevisionIssueType.INCORRECT_CONCLUSION,
                reason="注入事务失败以验证旧 Memory、修订候选和审计不会产生部分写入。",
            ),
            context=_reviewer_context(key=f"memory-revision:sqlite:failure:{fail_after_write}"),
        )

    reader_repository = SqlAlchemyAlertRepository(session_factory)
    reader = SocMemoryService(
        candidate_repository=reader_repository,
        record_repository=reader_repository,
    )
    persisted = reader.get_record(memory_id)
    assert persisted.version == original.version
    assert persisted.retrieval_enabled is True
    assert all(item.source.source_type is not SocMemoryCandidateSourceType.MEMORY_REVISION for item in reader.list_candidates(status=None, limit=100))
    assert (
        reader_repository.list_mutation_audits(
            operation=SocMutationOperation.MEMORY_REVISION_CANDIDATE_CREATE,
            target_id=memory_id,
        )
        == []
    )
