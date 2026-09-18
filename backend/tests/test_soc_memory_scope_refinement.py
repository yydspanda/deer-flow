from datetime import timedelta

import pytest
from test_soc_direct_resolution import memory_case
from test_soc_memory_governance import candidate
from test_soc_memory_governance import services as _services
from test_soc_memory_revision_workflow import _reviewer_context

from soc_agent.contracts import SocMemoryApplicabilitySpec, SocMemoryCandidateStatus
from soc_agent.contracts.memory_governance import MemoryScopeBoundaryReleaseCommand, MemoryScopeRefinementCommand
from soc_agent.contracts.schemas import SocMemoryReuseCondition
from soc_agent.core import SocServiceConflictError

services = _services


@pytest.fixture(autouse=True, params=["off", "apply"])
def normalization_mode(monkeypatch, request):
    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", request.param)


def test_refinement_is_pending_idempotent_and_does_not_replace_parent(services):
    service, repository = services
    parent = candidate(service, 501)
    command = MemoryScopeRefinementCommand(candidate_id=parent.candidate_id, expected_updated_at=parent.updated_at, promoted_facet_values={"source_type": parent.applicability.optional_facets["source_type"]})
    child = service.refine_candidate_scope(command, context=_reviewer_context(key="refine"))
    assert child.candidate_id != parent.candidate_id
    assert child.status is SocMemoryCandidateStatus.PENDING_REVIEW
    assert child.metadata["scope_refinement_parent_id"] == parent.candidate_id
    assert repository.get_memory_candidate(parent.candidate_id) == parent
    assert service.refine_candidate_scope(command, context=_reviewer_context(key="refine-again")).candidate_id == child.candidate_id
    assert repository.get_memory_record_by_candidate_id(child.candidate_id) is None
    with pytest.raises(SocServiceConflictError):
        service.refine_candidate_scope(command.model_copy(update={"expected_updated_at": parent.updated_at - timedelta(seconds=1)}), context=_reviewer_context(key="stale"))


def test_release_requires_explicit_versioned_review_and_is_audited(services):
    service, repository = services
    _, _, parent = memory_case(services)
    child = parent.model_copy(
        update={
            "memory_id": "MEM-EXCEPTION",
            "source_candidate_id": "MC-EXCEPTION",
            "retrieval_enabled": False,
            "applicability": SocMemoryApplicabilitySpec.model_validate(
                {
                    **parent.applicability.model_dump(),
                    "policy_version": "soc.memory_applicability_policy.v4" if parent.applicability.covered_behavior_components is not None else "soc.memory_applicability_policy.v2",
                    "reuse_conditions": [SocMemoryReuseCondition(facet_key="entity", value_prefix="host", values=["host:one"])],
                }
            ),
        }
    )
    repository.save_memory_record(child)
    command = MemoryScopeBoundaryReleaseCommand(memory_id=parent.memory_id, expected_version=parent.version, exception_memory_id=child.memory_id, expected_exception_version=child.version, reason="模拟审核确认此主机可恢复沿用通用业务经验")
    assert not service.scope_boundaries(parent.memory_id).exceptions[0].released
    updated = service.release_scope_boundary(command, context=_reviewer_context(key="release"))
    assert updated.version == parent.version + 1
    assert service.scope_boundaries(parent.memory_id).exceptions[0].released
    assert service.release_scope_boundary(command, context=_reviewer_context(key="release")).version == updated.version
    with pytest.raises(SocServiceConflictError):
        service.release_scope_boundary(command, context=_reviewer_context(key="stale-release"))
    repository.save_memory_record(child.model_copy(update={"version": child.version + 1}))
    assert not service.scope_boundaries(parent.memory_id).exceptions[0].released


def test_subset_draft_retains_only_selected_source_conclusions(services):
    from types import SimpleNamespace

    from test_soc_memory_coverage import scope

    from soc_agent.contracts.schemas import SocMemoryScopeBinding
    from soc_agent.memory.scope_options import candidate_with_scope_selections, scope_lesson_sources

    parent = candidate(services[0], 801)
    observations = {}
    spec = scope(reuse_conditions=[SocMemoryReuseCondition(facet_key="role_entity", value_prefix="destination", values=["destination:192.0.2.1"])])
    parent = parent.model_copy(update={"applicability": spec, "metadata": {"observation_ids": ["o1", "o2"]}, "content": "MUST-NOT-LEAK-OTHER-SAMPLE"})
    for index in (1, 2):
        value = f"destination:192.0.2.{index}"
        observations[f"o{index}"] = SimpleNamespace(
            tenant_id=parent.tenant_id,
            profile_id=spec.profile_id,
            profile_version=spec.profile_version,
            feature_schema_version=spec.feature_schema_version,
            environment="dev",
            source=SimpleNamespace(source_id=f"o{index}", alert_id=f"alert-{index}", run_id=f"run-{index}"),
            signature=SimpleNamespace(
                facets={"detection_key": ["rule:test"], "behavior_component_core": spec.selected_behavior_components, "role_entity": [value]},
                scope_bindings=[SocMemoryScopeBinding(source_ref=f"connection:{index}", facets={"role_entity": [value]})],
            ),
            lesson=None,
        )
    repo = SimpleNamespace(get_memory_pattern_observation=observations.get)
    expanded = candidate_with_scope_selections(parent, repo, {"role_entity": ["destination:192.0.2.1"]})
    scoped = scope_lesson_sources(expanded, repo, spec)
    assert scoped.metadata["observation_ids"] == ["o1"]
    assert scoped.metadata["support_count"] == 1
    assert "alert-1" in scoped.content
    assert "alert-2" not in scoped.content
    assert "MUST-NOT-LEAK" not in scoped.content
    assert scoped.facets["role_entity"] == ["destination:192.0.2.1"]
