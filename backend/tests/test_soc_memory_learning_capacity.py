"""Learning capacity must not rewrite exact matching values or raw evidence."""

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test_soc_agent_service import InMemoryAlertRepository, InMemorySummaryRepository
from test_soc_memory_patterns import _context, _run, _service

from soc_agent.contracts import (
    ActorContext,
    AlertSummary,
    CorrectionCommand,
    Decision,
    EntityKind,
    EntityMention,
    MemoryPatternDataClass,
    MemoryPatternSourceType,
    ProcessingJobStatus,
    ReviewNoteCommand,
    ReviewQueueItem,
    ServiceRequestContext,
    SocMemoryRunPromotionCommand,
    Verdict,
)
from soc_agent.core import SocReviewService
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.db.corpus_experiments import CorpusRunJob
from soc_agent.demo.corpus_workbench import _execution_view
from soc_agent.memory import memory_query_from_analysis_request
from soc_agent.memory.patterns import EXACT_MEMORY_FACET_TOO_LONG, InMemoryMemoryPatternRepository, memory_pattern_command_from_run
from soc_agent.memory.profiles import GenericSocMemoryProfile, SocMemoryProfileRegistry
from soc_agent.memory.sources import SocMemoryCandidateSourceBridge, memory_candidate_command_from_review_note, memory_candidate_command_from_run_promotion


class _ExactProfile(GenericSocMemoryProfile):
    def __init__(self, facet, value):
        self.facet = facet
        self.value = value

    def project_run_facets(self, run):
        return {**super().project_run_facets(run), self.facet: [self.value]}


def _observe_command(run, profile):
    return memory_pattern_command_from_run(
        run,
        source_type=MemoryPatternSourceType.BATCH_ALERT,
        transport_ref=f"capacity-test:{run.run_id}",
        environment="dev",
        data_class=MemoryPatternDataClass.OPERATIONAL,
        policy_fingerprint="a" * 64,
        profile=profile,
    )


@pytest.mark.parametrize("facet", ["entity", "role_entity"])
def test_oversized_exact_value_is_filtered_only_from_new_learning(facet):
    run = _run(1)
    saved = run.model_dump(mode="json")
    value = "url:https://private.example/" + "x" * 600
    profile = _ExactProfile(facet, value)

    automatic = _observe_command(run, profile)
    manual = memory_candidate_command_from_run_promotion(run, SocMemoryRunPromotionCommand(run_id=run.run_id), profile_registry=SocMemoryProfileRegistry(fallback=profile))

    assert value not in automatic.signature.facets.get(facet, [])
    assert value not in manual.facets.get(facet, [])
    assert automatic.signature.facets["detection_key"] == profile.project_run_facets(run)["detection_key"]
    assert manual.facets["detection_key"] == profile.project_run_facets(run)["detection_key"]
    assert manual.applicability is not None
    assert profile.project_run_facets(run)[facet] == [value]
    assert run.model_dump(mode="json") == saved


def test_pattern_and_manual_keep_their_existing_whitespace_boundaries():
    run = _run(1)
    value = "url:https://example.test/" + " " * 600 + "path"
    profile = _ExactProfile("entity", value)

    command = _observe_command(run, profile)

    assert command.signature.facets["entity"] == [" ".join(value.split())]
    assert profile.project_run_facets(run)["entity"] == [value]
    manual = memory_candidate_command_from_run_promotion(run, SocMemoryRunPromotionCommand(run_id=run.run_id), profile_registry=SocMemoryProfileRegistry(fallback=profile))
    assert "entity" not in manual.facets


def test_other_pattern_contract_errors_remain_processing_failures():
    profile = _ExactProfile("behavior_component", "control:" + "x" * 600)
    with pytest.raises(ValidationError):
        _observe_command(_run(1), profile)


def test_completed_ineligible_job_keeps_analysis_complete_and_explains_memory_skip():
    run = _run(1)
    saved = run.model_dump(mode="json")
    execution = _execution_view(
        alert_id=run.alert_id,
        run=run,
        observation=None,
        replay=None,
        candidate=None,
        pattern_job=CorpusRunJob(ProcessingJobStatus.COMPLETED, "learning", EXACT_MEMORY_FACET_TOO_LONG),
    )

    memory = next(phase for phase in execution.phases if phase.phase == "memory")
    assert execution.status == "analysis_complete"
    assert execution.run_status == run.status.value
    assert execution.current_phase is None
    assert memory.status == "skipped"
    assert memory.steps[0].status == "skipped"
    assert memory.summary == EXACT_MEMORY_FACET_TOO_LONG
    assert run.model_dump(mode="json") == saved


def test_result_read_allows_manual_experience_after_filtering_oversized_entity():
    run = _run(1)
    run.decision = Decision(verdict=run.analysis.verdict, confidence=run.analysis.confidence, suggested_action=run.analysis.recommended_action, needs_review=True, reason=run.analysis.reason)
    saved = run.model_dump(mode="json")
    profile = _ExactProfile("entity", "url:https://private.example/" + "x" * 600)
    repository = InMemoryAlertRepository()
    repository.save_run(run)
    summaries = InMemorySummaryRepository()
    summaries.save_alert_summary(AlertSummary(run_id=run.run_id, alert_id=run.alert_id, status=run.status, verdict=run.analysis.verdict))
    candidates = InMemoryMemoryPatternRepository()
    review = SocReviewService(repository=repository, summary_repository=summaries, memory_candidate_repository=candidates, memory_profile_registry=SocMemoryProfileRegistry(fallback=profile))

    result = review.get_alert_investigation_context(run.run_id)

    assert result.run.run_id == run.run_id
    assert result.run.analysis == run.analysis
    assert result.learning.state == "accumulating"
    assert result.learning.action == "promote"
    assert result.learning.candidate_id is None
    assert "private.example" not in result.learning.model_dump_json()
    assert candidates.list_memory_candidates() == []
    assert repository.get_run(run.run_id).model_dump(mode="json") == saved


@pytest.mark.parametrize("promote_to_memory", [False, True])
def test_filtered_exact_condition_preserves_sqlite_correction_admission_and_audit(tmp_path, promote_to_memory):
    engine = create_engine(f"sqlite:///{tmp_path / 'correction.sqlite'}")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    run = _run(1)
    run.decision = Decision(verdict=run.analysis.verdict, confidence=run.analysis.confidence, suggested_action=run.analysis.recommended_action, needs_review=True, reason=run.analysis.reason)
    repository.save_run(run)
    original_input = run.input_payload.copy()
    profile = _ExactProfile("entity", "url:https://private.example/" + "x" * 600)
    review = SocReviewService(repository=repository, summary_repository=repository, audit_repository=repository, memory_candidate_repository=repository, memory_profile_registry=SocMemoryProfileRegistry(fallback=profile))
    context = ServiceRequestContext(actor=ActorContext(actor_id="capacity-reviewer", roles=["soc_analyst"]), idempotency_key="capacity-correction")
    command = CorrectionCommand(run_id=run.run_id, corrected_verdict=Verdict.FALSE_POSITIVE, reason="Analyst confirmed this was an authorized internal test activity.", promote_to_memory=promote_to_memory)

    corrected = review.correct(command, context=context)
    saved = repository.get_run(run.run_id)

    assert saved.decision.verdict is Verdict.FALSE_POSITIVE
    assert saved.analysis == run.analysis
    assert saved.input_payload == original_input
    assert len(saved.corrections) == 1
    correction = saved.corrections[0]
    expected_status = "admitted" if promote_to_memory else "observed_only"
    assert correction.memory_admission.status.value == expected_status
    assert "feature_value_too_long" not in [reason.value for reason in correction.memory_admission.reason_codes]
    assert correction.candidate_knowledge_status == ("pending_review" if promote_to_memory else "observed_only")
    assert "private.example" not in correction.memory_admission.model_dump_json()
    candidates = repository.list_memory_candidates()
    if promote_to_memory:
        assert len(candidates) == 1
        candidate = candidates[0]
        assert correction.memory_candidate_id == candidate.candidate_id
        assert candidate.status.value == "pending_review"
        assert candidate.applicability is not None
        assert not candidate.facets.get("entity")
        assert candidate.facets["detection_key"] == profile.project_run_facets(run)["detection_key"]
    else:
        assert correction.memory_candidate_id is None
        assert candidates == []
    assert repository.list_memory_pattern_observations() == []
    audit = repository.list_audit_records(run.run_id)[0]
    assert audit.correction_id == correction.correction_id
    assert audit.final_verdict is Verdict.FALSE_POSITIVE
    assert audit.payload["candidate_knowledge_status"] == correction.candidate_knowledge_status
    assert repository.list_mutation_audits(run_id=run.run_id)[0].payload["memory_admission_status"] == expected_status
    retried = review.correct(command, context=context)
    assert len(retried.corrections) == 1
    assert retried.corrections[0].correction_id == corrected.corrections[0].correction_id
    assert retried.corrections[0].memory_admission.command_hash == correction.memory_admission.command_hash
    engine.dispose()


def test_filtering_the_only_entity_does_not_bypass_existing_anchor_gates():
    class UnanchoredProfile(GenericSocMemoryProfile):
        def project_run_facets(self, run):
            return {"entity": ["url:https://example.test/" + "a" * 600]}

    profile = UnanchoredProfile()
    repository = InMemoryMemoryPatternRepository()
    pattern_service = _service(repository)
    for index in range(1, 4):
        command = _observe_command(_run(index, detection_key=None), profile)
        result = pattern_service.ingest_observation(command, context=_context())
        assert result.observation is not None
        assert not result.observation.signature.facets.get("entity")
        assert result.candidate is None
    assert repository.list_memory_candidates() == []

    class NeverPropose:
        def propose_candidate(self, *args, **kwargs):
            pytest.fail("Unanchored feedback must not reach candidate persistence")

    bridge = SocMemoryCandidateSourceBridge(NeverPropose(), profile_registry=SocMemoryProfileRegistry(fallback=profile))
    outcome = bridge.admit_from_run_promotion(_run(1), SocMemoryRunPromotionCommand(run_id=_run(1).run_id))
    assert outcome.candidate is None
    assert outcome.decision.status.value == "observed_only"
    assert "no_reusable_anchor" in {reason.value for reason in outcome.decision.reason_codes}


def test_review_note_filters_only_oversized_learning_entities_and_preserves_lineage():
    run = _run(1)
    legal = "https://example.test/" + "x" * (512 - len("url:https://example.test/"))
    oversized = "https://example.test/query?sql=" + "y" * 600
    run.input_payload["urls"] = [legal, oversized]
    run.llm_analysis_request.extracted_entities.mentions = [EntityMention(kind=EntityKind.URL, key="url:" + value, value=value) for value in (legal, oversized)]
    frozen_run = run.model_dump(mode="json")
    profile = GenericSocMemoryProfile()
    query = memory_query_from_analysis_request(run.llm_analysis_request, profile=profile)
    frozen_query = query.model_dump(mode="json")
    queue = ReviewQueueItem(queue_id="REV-CAPACITY-NOTE", run_id=run.run_id, alert_id=run.alert_id, reason="Analyst review requested")
    note = ReviewNoteCommand(queue_id=queue.queue_id, note="运营已核实这两条完整访问记录属于已授权测试，请保留完整证据供审核。", scenario_key="authorized_test", finding_id="F-CAPACITY", promote_to_memory=True)

    command = memory_candidate_command_from_review_note(run, note, queue_item=queue)

    assert len("url:" + legal) == 512
    assert "url:" + legal in command.facets["entity"]
    assert "url:" + oversized not in command.facets["entity"]
    assert note.note in command.content
    assert command.source.run_id == run.run_id
    assert command.source.alert_id == run.alert_id
    assert command.source.queue_id == queue.queue_id
    assert {f"review_note:{queue.queue_id}", f"review_queue:{queue.queue_id}", f"run:{run.run_id}", f"alert:{run.alert_id}", "domain_finding:F-CAPACITY", "scenario:authorized_test"} <= set(command.evidence_refs)
    assert "url:" + legal in query.facets["entity"]
    assert "url:" + oversized in query.facets["entity"]
    assert run.model_dump(mode="json") == frozen_run
    assert memory_query_from_analysis_request(run.llm_analysis_request, profile=profile).model_dump(mode="json") == frozen_query
