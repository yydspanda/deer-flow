"""Reviewed behavior selection is set-based, independent of grouping hashes."""

from types import SimpleNamespace

import pytest
from test_soc_memory_governance import candidate, confirm
from test_soc_memory_governance import services as services
from test_soc_memory_scope_view import sample

from soc_agent.contracts import SocMemoryApplicabilitySpec, SocMemoryQuery
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.memory.behavior_scope import select_memory_behavior_components
from soc_agent.memory.governance import scope_identity, scope_relation
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.memory.scoring import evaluate_memory_scope
from soc_agent.utils.hashing import stable_hash


def fixture():
    spec, facets = sample()
    facets = {**facets, **spec.required_facets}
    facets["behavior_component_core"] = list(facets["behavior_component"])
    return spec, facets, SocMemoryProfileRegistry([PingAnSocMemoryProfile()])


def evaluate(spec, facets):
    return evaluate_memory_scope(
        spec,
        "detection_lesson",
        SocMemoryQuery(
            facets=facets,
            metadata={
                "memory_profile_id": spec.profile_id,
                "memory_profile_version": spec.profile_version,
                "memory_feature_schema_version": spec.feature_schema_version,
            },
        ),
        {},
    )


def test_selection_order_and_duplicates_do_not_change_scope_or_matching():
    spec, facets, registry = fixture()
    values = facets["behavior_component_core"]
    a = select_memory_behavior_components(spec, facets, values, registry=registry)
    b = select_memory_behavior_components(spec, facets, [*reversed(values), values[0]], registry=registry)
    assert a == b
    assert evaluate(a, facets).status.value == "applicable"
    identity = dict(tenant_scope="pingan", tenant_id="pingan", metadata={})
    assert scope_identity(SimpleNamespace(applicability=a, **identity)) == scope_identity(SimpleNamespace(applicability=b, **identity))


def test_unchecked_feature_really_stops_requiring_the_old_hash():
    spec, facets, registry = fixture()
    selected = [v for v in facets["behavior_component_core"] if not v.startswith("network_service:")]
    reviewed = select_memory_behavior_components(spec, facets, selected, registry=registry)
    changed = {**facets, "behavior_fingerprint": ["different"]}
    changed["behavior_component_core"] = [*selected, "network_service:http/443"]
    changed["behavior_component"] = changed["behavior_component_core"]
    assert evaluate(reviewed, changed).status.value == "applicable"
    assert evaluate(spec, changed).status.value == "partial"
    assert reviewed.required_facets == spec.required_facets  # grouping lineage survives


def test_selected_components_are_all_required_not_any_one():
    spec, facets, registry = fixture()
    reviewed = select_memory_behavior_components(spec, facets, facets["behavior_component_core"], registry=registry)
    changed = {**facets, "behavior_component_core": ["technique:t1190"]}
    report = evaluate(reviewed, changed)
    assert report.status.value == "partial"
    assert "network_service:http/8080" in report.missing_behavior_components
    assert report.context_only_allowed


@pytest.mark.parametrize("values", [[], ["process:invented.exe"]])
def test_review_rejects_empty_or_invented_behavior(values):
    spec, facets, registry = fixture()
    with pytest.raises(ValueError):
        select_memory_behavior_components(spec, facets, values, registry=registry)


def test_unknown_hash_cannot_be_turned_into_editable_scope():
    spec, facets, registry = fixture()
    spec.required_facets["behavior_fingerprint"] = ["unverified"]
    with pytest.raises(ValueError, match="verified"):
        select_memory_behavior_components(spec, facets, ["protocol:http"], registry=registry)


def test_selected_scopes_overlap_even_when_source_hashes_differ():
    spec, facets, registry = fixture()
    left = select_memory_behavior_components(spec, facets, ["protocol:http"], registry=registry)
    right = SocMemoryApplicabilitySpec.model_validate({**left.model_dump(), "required_facets": {**left.required_facets, "behavior_fingerprint": ["different"]}})
    assert scope_relation(left, right, registry) == "same"
    assert scope_relation(left, spec, registry) == "overlap"


def test_existing_fingerprint_explanation_ignores_order_and_duplicates():
    spec, facets, _ = fixture()
    facets["behavior_component_core"] = [*reversed(facets["behavior_component_core"]), facets["behavior_component_core"][0]]
    from soc_agent.integrations.pingan.memory.scope_view import explain_scope_facets

    assert explain_scope_facets(spec, facets)["behavior_fingerprint"]
    assert stable_hash({"b": 2, "a": 1}) == stable_hash({"a": 1, "b": 2})


def test_real_review_retrieval_and_directive_do_not_require_old_fingerprint(services):
    from datetime import UTC, datetime

    from test_soc_automation import _runtime_run

    from soc_agent.automation import InMemorySocAutomationRepository
    from soc_agent.contracts import ServiceRequestContext, Verdict
    from soc_agent.core.automation import SocAutomationService
    from soc_agent.memory.retrieval import _memory_context_item

    service, repository = services
    item = candidate(service, 901, network_protocol="tcp")
    registry = SocMemoryProfileRegistry([PingAnSocMemoryProfile()])
    values = [v for v in item.facets["behavior_component_core"] if not v.startswith("protocol:")]
    reviewed = select_memory_behavior_components(item.applicability, item.facets, values, registry=registry)
    record = confirm(service, item, Verdict.FALSE_POSITIVE, record_applicability=reviewed)
    assert "behavior_fingerprint" not in record.decision_directive.required_facet_keys
    assert "behavior_component" in record.decision_directive.required_facet_keys
    facets = {**record.facets, "behavior_fingerprint": ["changed"], "behavior_component_core": [*values, "protocol:udp"], "behavior_component": [*values, "protocol:udp"]}
    query = SocMemoryQuery(
        tenant_id=record.tenant_id,
        tenant_scope=record.tenant_scope,
        facets=facets,
        metadata={
            "memory_profile_id": reviewed.profile_id,
            "memory_profile_version": reviewed.profile_version,
            "memory_feature_schema_version": reviewed.feature_schema_version,
        },
    )
    matches = service.find_relevant_records(query).matches
    assert len(matches) == 1
    assert matches[0].applicability_report.status.value == "applicable"
    context_item = _memory_context_item(matches[0], query_facets=facets, retrieval_policy_version=query.policy_version)
    assert context_item.memory_comparison.decision_directive_applicable
    run = _runtime_run()
    run.llm_analysis_request.context_catalog = [context_item]
    automation = SocAutomationService(repository=InMemorySocAutomationRepository(), policy=None, environment="prd", memory_repository=repository)
    assert automation._eligible_memory_directives(run, now=datetime.now(UTC))
    result = automation.evaluate(run, context=ServiceRequestContext())
    assert result.decision_transition.after.verdict == Verdict.FALSE_POSITIVE


def test_reviewed_selection_cannot_change_fixed_scope_or_invent_features():
    from soc_agent.core.service import SocServiceError, _validate_memory_record_applicability

    spec, facets, registry = fixture()
    item = SimpleNamespace(applicability=spec, facets=facets)
    reviewed = select_memory_behavior_components(spec, facets, ["protocol:http"], registry=registry)
    _validate_memory_record_applicability(item, reviewed, registry=registry)
    changed = reviewed.model_copy(update={"selected_behavior_components": ["protocol:invented"]})
    with pytest.raises(SocServiceError):
        _validate_memory_record_applicability(item, changed, registry=registry)
    changed = reviewed.model_copy(update={"required_facets": {**spec.required_facets, "environment": ["prd"]}})
    with pytest.raises(SocServiceError, match="preserve"):
        _validate_memory_record_applicability(item, changed, registry=registry)


def test_opposing_selected_scopes_cannot_hide_behind_different_grouping_hashes(services):
    from soc_agent.contracts import Verdict
    from soc_agent.core import SocServiceConflictError

    service, _ = services
    registry = SocMemoryProfileRegistry([PingAnSocMemoryProfile()])
    a = candidate(service, 902, network_protocol="tcp")
    selected = [v for v in a.facets["behavior_component_core"] if not v.startswith("protocol:")]
    scope = select_memory_behavior_components(a.applicability, a.facets, selected, registry=registry)
    confirm(service, a, Verdict.FALSE_POSITIVE, record_applicability=scope)
    b = candidate(service, 903, Verdict.TRUE_POSITIVE, network_protocol="udp")
    other = select_memory_behavior_components(b.applicability, b.facets, selected, registry=registry)
    assert a.applicability.required_facets["behavior_fingerprint"] != b.applicability.required_facets["behavior_fingerprint"]
    with pytest.raises(SocServiceConflictError):
        confirm(service, b, Verdict.TRUE_POSITIVE, record_applicability=other)


def test_production_fingerprint_generation_sorts_and_deduplicates_components():
    from test_soc_pingan_memory_profile import _run

    from soc_agent.integrations.pingan.memory.profile import _project_pingan_facets

    request = _run(904).llm_analysis_request
    values = ["protocol:udp", "technique:t1190", "network_service:udp/1194"]
    a = _project_pingan_facets({"behavior_component": values}, request=request)
    b = _project_pingan_facets({"behavior_component": [*reversed(values), *values]}, request=request)
    assert a["behavior_fingerprint"] == b["behavior_fingerprint"]


def test_drafting_and_governance_preview_share_selected_scope_without_writes(services):
    from test_soc_memory_revision_workflow import _reviewer_context

    from soc_agent.contracts import Verdict
    from soc_agent.core.memory_lesson_drafting import SocMemoryLessonDraftService
    from soc_agent.memory.lessons import memory_lesson_applicability_conditions

    service, repository = services
    item = candidate(service, 905, network_protocol="tcp")
    selected = [v for v in item.facets["behavior_component_core"] if not v.startswith("protocol:")]
    captured = []

    class Drafter:
        def draft(self, source, **kwargs):
            captured.append(source)

    draft_service = SocMemoryLessonDraftService(candidate_repository=repository, drafter=Drafter(), governance_service=service, profile_registry=SocMemoryProfileRegistry([PingAnSocMemoryProfile()]))
    draft_service.draft_business_lesson(item.candidate_id, reviewer_verdict=Verdict.FALSE_POSITIVE, selected_behavior_components=selected, context=_reviewer_context(key="selected-draft"))
    assert captured[0].applicability.selected_behavior_components == sorted(selected)
    assert "governance_comparison" in captured[0].metadata
    prose = memory_lesson_applicability_conditions(captured[0].applicability)
    assert not any("behavior_fingerprint" in value for value in prose)
    assert any("逐项全部满足" in value for value in prose)
    assert repository.get_memory_candidate(item.candidate_id).applicability.selected_behavior_components is None
    assert repository.get_memory_record_by_candidate_id(item.candidate_id) is None


def test_selected_behavior_and_ip_limits_both_apply_without_blocking_reference():
    from soc_agent.memory.lessons import promote_memory_applicability_facets

    spec, facets, registry = fixture()
    spec.optional_facets["role_entity"] = ["source:192.0.2.1", "destination:192.0.2.2"]
    reviewed = select_memory_behavior_components(spec, facets, ["protocol:http"], registry=registry)
    reviewed = promote_memory_applicability_facets(reviewed, [], {"role_entity": spec.optional_facets["role_entity"]})
    query = {**facets, "behavior_fingerprint": ["different"], "role_entity": ["source:192.0.2.1"]}
    report = evaluate(reviewed, query)
    assert report.status.value == "partial"
    assert report.context_only_allowed
    assert report.missing_reuse_conditions[0].value_prefix == "destination"
    query["role_entity"].append("destination:192.0.2.2")
    assert evaluate(reviewed, query).status.value == "applicable"
