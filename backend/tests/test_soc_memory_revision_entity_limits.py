"""New scope revisions filter learning entities without rewriting old Memory."""

from datetime import UTC, datetime

import pytest
from test_soc_memory_revision_workflow import RevisionRepository, _active_memory_fixture, _reviewer_context

from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import EntityKind, EntityMention, SocMemoryCandidateStatus, SocMemoryRevisionCandidateCreateCommand, SocMemoryRevisionIssueType
from soc_agent.contracts.schemas import RoleResolution, RoleResolutionStatus


def _revision_case(facet_key):
    repository = RevisionRepository()
    service, old_candidate_id, memory_id = _active_memory_fixture(repository, now=datetime(2026, 8, 21, 8, 30, tzinfo=UTC))
    run = repository.get_run("RUN-WRONG-MEMORY")
    if facet_key == "entity":
        short = "url:" + "x" * 508
        long = short + "x"
        run.llm_analysis_request.extracted_entities.mentions = [EntityMention(kind=EntityKind.URL, value=value.removeprefix("url:"), key=value) for value in (short, long)]
    else:
        short = "attacker:" + "x" * 503
        long = short + "x"
        run.llm_analysis_request.fact_reconstruction.role_resolutions = [
            RoleResolution(role="attacker", status=RoleResolutionStatus.CONFIRMED, selected_value=value.removeprefix("attacker:"), rationale="合成来源样本中的已确认角色。", confidence=0.9) for value in (short, long)
        ]
    return service, repository, old_candidate_id, memory_id, run, short, long


@pytest.mark.parametrize("facet_key", ["entity", "role_entity"])
def test_applicability_revision_filters_only_new_optional_entity_values(facet_key):
    service, repository, old_candidate_id, memory_id, run, short, long = _revision_case(facet_key)
    predecessor = service.get_record(memory_id)
    old_candidate = service.get_candidate(old_candidate_id).model_dump_json()
    saved_run = run.model_dump_json()
    profile = build_soc_memory_profile_registry().resolve_run(run)
    raw_facets = profile.project_run_facets(run)
    assert short in raw_facets[facet_key] and long in raw_facets[facet_key]
    command = SocMemoryRevisionCandidateCreateCommand(
        memory_id=memory_id,
        expected_record_version=predecessor.version,
        source_run_id=run.run_id,
        issue_type=SocMemoryRevisionIssueType.APPLICABILITY_TOO_BROAD,
        reason="根据此次误命中的来源行为重新收窄适用范围，保留有效实体条件。",
    )
    context = _reviewer_context(key="revision:bounded-new-entities")

    result = service.propose_revision_candidate(command, context=context)

    candidate = result.candidate
    assert candidate.status is SocMemoryCandidateStatus.PENDING_REVIEW
    assert short in candidate.facets[facet_key]
    assert long not in candidate.facets[facet_key]
    assert candidate.applicability.optional_facets[facet_key] == [short]
    assert candidate.applicability.required_facets["detection_key"] == raw_facets["detection_key"]
    assert candidate.applicability.required_facets["behavior_fingerprint"] == raw_facets["behavior_fingerprint"]
    assert candidate.metadata["revision_scope_source"] == "source_run_profile_projection"
    suspended = service.get_record(memory_id)
    assert suspended.applicability == predecessor.applicability
    assert suspended.facets == predecessor.facets
    assert suspended.decision_directive == predecessor.decision_directive
    assert suspended.content_hash == predecessor.content_hash
    assert suspended.facets_hash == predecessor.facets_hash
    assert not suspended.retrieval_enabled
    assert service.get_candidate(old_candidate_id).model_dump_json() == old_candidate
    assert repository.get_run(run.run_id).model_dump_json() == saved_run
    assert profile.project_run_facets(run) == raw_facets
    assert service.propose_revision_candidate(command, context=context).candidate == candidate


@pytest.mark.parametrize("issue_type", [SocMemoryRevisionIssueType.INCORRECT_CONCLUSION, SocMemoryRevisionIssueType.LESSON_INCOMPLETE])
def test_other_revisions_keep_predecessor_scope_with_long_source_entities(issue_type):
    service, repository, _, memory_id, run, _, _ = _revision_case("entity")
    predecessor = service.get_record(memory_id)
    saved_run = run.model_dump_json()

    result = service.propose_revision_candidate(
        SocMemoryRevisionCandidateCreateCommand(
            memory_id=memory_id,
            expected_record_version=predecessor.version,
            source_run_id=run.run_id,
            issue_type=issue_type,
            reason="修订原有业务结论和解释，继续保留人工审核的原适用条件。",
        ),
        context=_reviewer_context(key="revision:preserve-reviewed-scope"),
    )

    assert result.candidate.metadata["revision_scope_source"] == "predecessor_snapshot"
    assert result.candidate.facets == predecessor.facets
    assert result.candidate.applicability == predecessor.applicability
    assert service.get_record(memory_id).applicability == predecessor.applicability
    assert repository.get_run(run.run_id).model_dump_json() == saved_run
