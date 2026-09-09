from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from soc_agent.contracts import (
    ActorContext,
    AlertClassification,
    AlertEntitySet,
    AlertSourceRef,
    DetectionRuleRef,
    LLMAnalysisRequest,
    NetworkEntityRef,
    ScenarioHypothesis,
    SocMemoryApplicabilitySpec,
    SocMemoryCandidateSource,
    SocMemoryCandidateValidity,
    SocMemoryDecisionDirective,
    SocMemoryDecisionImpact,
    SocMemoryRecord,
    Verdict,
)
from soc_agent.core.service import SocMemoryService
from soc_agent.memory import ConfirmedMemoryAnalysisRequestEnricher, InMemoryMemoryCandidateRepository, memory_query_from_analysis_request
from soc_agent.memory.retrieval import _select_memory_reasoning_context
from soc_agent.pipeline.reference_catalog import finalize_analysis_reference_catalogs
from soc_agent.prompts import build_analysis_prompt


def _request() -> LLMAnalysisRequest:
    return LLMAnalysisRequest(
        alert_id="SYNTHETIC-OPENVPN-1194",
        tenant_id="example",
        environment="dev",
        source=AlertSourceRef(source_type="ndr", source_system="example-sensor"),
        detection=DetectionRuleRef(detection_key="example:red-team-ip"),
        classification=AlertClassification(technique=["T1190"]),
        canonical_entities=AlertEntitySet(network=NetworkEntityRef(protocol="udp", dst_port=1194)),
    )


def _record(memory_id: str, verdict: Verdict | None, *, exact: bool, request: LLMAnalysisRequest) -> SocMemoryRecord:
    query = memory_query_from_analysis_request(request)
    now = datetime.now(UTC)
    facets = {**query.facets, "behavior_fingerprint": query.facets["behavior_fingerprint"] if exact else ["synthetic-openvpn-plus-sip"]}
    scope_key = "detection_key" if "detection_key" in facets else "scenario_key"
    spec = SocMemoryApplicabilitySpec(
        profile_id=query.metadata["memory_profile_id"],
        profile_version=query.metadata["memory_profile_version"],
        feature_schema_version=query.metadata["memory_feature_schema_version"],
        required_facets={key: facets[key] for key in (scope_key, "environment", "behavior_fingerprint")},
        optional_facets={"behavior_component": facets["behavior_component"]},
        context_only_required_facet_keys=[scope_key, "environment"],
        context_only_missing_facet_keys=["behavior_fingerprint"],
        context_only_similarity_facet_keys=["behavior_component"],
    )
    return SocMemoryRecord(
        memory_id=memory_id,
        memory_type="detection_lesson",
        target_artifact="tenant_memory",
        tenant_scope="example",
        tenant_id="example",
        source_candidate_id=f"MC-{memory_id}",
        source=SocMemoryCandidateSource(source_type="correction", source_id=f"COR-{memory_id}"),
        summary=f"Reviewed synthetic lesson {memory_id}",
        content=f"SYNTHETIC-LESSON-CONTENT-{memory_id}",
        facets=facets,
        applicability=spec,
        reviewed_verdict=verdict,
        evidence_refs=["synthetic:review"],
        validity=SocMemoryCandidateValidity(valid_from=now - timedelta(days=1), notes="Synthetic active lesson"),
        content_hash="sha256:" + "a" * 64,
        facets_hash="sha256:" + "b" * 64,
        retrieval_enabled=True,
        retrieval_policy_version="soc.memory_retrieval_activation_policy.v1",
        retrieval_valid_until=now + timedelta(days=30),
        retrieval_review_due_at=now + timedelta(days=7),
        retrieval_updated_by=ActorContext(actor_id="synthetic-reviewer"),
        retrieval_updated_at=now,
        retrieval_reason="Synthetic reviewed fixture",
        created_by=ActorContext(actor_id="synthetic-reviewer"),
    )


def _enrich(records: list[SocMemoryRecord], request: LLMAnalysisRequest) -> LLMAnalysisRequest:
    repository = InMemoryMemoryCandidateRepository()
    for record in records:
        repository.save_memory_record(record)
    service = SocMemoryService(record_repository=repository)
    return ConfirmedMemoryAnalysisRequestEnricher(service)(request)


@pytest.mark.parametrize("exact_verdict,partial_verdict", [(Verdict.TRUE_POSITIVE, Verdict.FALSE_POSITIVE), (Verdict.FALSE_POSITIVE, Verdict.TRUE_POSITIVE)])
@pytest.mark.parametrize("directive_enabled", [False, True])
def test_exact_reviewed_answer_excludes_opposing_partial_from_prompt_only(exact_verdict, partial_verdict, directive_enabled):
    request = _request()
    partial = _record("MEM-PARTIAL", partial_verdict, exact=False, request=request)
    exact = _record("MEM-EXACT", exact_verdict, exact=True, request=request)
    if directive_enabled:
        exact.decision_impact = SocMemoryDecisionImpact.DETECTION_DECISION
        exact.decision_directive = SocMemoryDecisionDirective(effect="override", target_verdict=exact_verdict, required_facet_keys=["behavior_fingerprint"], rationale="Synthetic reviewed directive")
    enriched = _enrich([partial, exact], request)

    assert [item.source_id for item in enriched.context_catalog] == ["MEM-EXACT@v1"]
    assert enriched.context_catalog[0].memory_comparison.decision_directive_applicable is directive_enabled
    assert len(enriched.memory_context_exclusions) == 1
    excluded = enriched.memory_context_exclusions[0]
    assert excluded.source_id == "MEM-PARTIAL@v1"
    assert excluded.preferred_source_ids == ["MEM-EXACT@v1"]
    assert excluded.reason_code == "opposing_partial_superseded_by_exact"
    assert excluded.memory_comparison.reviewed_verdict == partial_verdict
    assert excluded.memory_comparison.missing_required_facet_keys == ["behavior_fingerprint"]
    assert partial.retrieval_enabled is True

    restored = LLMAnalysisRequest.model_validate_json(enriched.model_dump_json())
    finalized = finalize_analysis_reference_catalogs(restored)
    prompt = build_analysis_prompt(finalized)
    assert "SYNTHETIC-LESSON-CONTENT-MEM-PARTIAL" not in prompt.user
    assert "MEM-PARTIAL" not in prompt.user
    assert "memory_context_exclusions" not in prompt.context
    assert "SYNTHETIC-LESSON-CONTENT-MEM-EXACT" in prompt.user
    assert prompt.example_id == ("context_memory_true_positive" if exact_verdict is Verdict.TRUE_POSITIVE else "context_memory")
    assert "contrasting lesson from another scope" in prompt.user
    assert json.loads(restored.model_dump_json())["memory_context_exclusions"]


@pytest.mark.parametrize("exact_verdict", [None, Verdict.UNKNOWN, Verdict.SUSPICIOUS, Verdict.FALSE_POSITIVE])
def test_undetermined_or_agreeing_exact_answer_does_not_remove_partial(exact_verdict):
    request = _request()
    records = [
        _record("MEM-PARTIAL", Verdict.FALSE_POSITIVE, exact=False, request=request),
        _record("MEM-EXACT", exact_verdict, exact=True, request=request),
    ]
    enriched = _enrich(records, request)
    assert len(enriched.context_catalog) == 2
    assert enriched.memory_context_exclusions == []


def test_without_exact_answer_partial_experience_remains_available():
    request = _request()
    enriched = _enrich([_record("MEM-PARTIAL", Verdict.FALSE_POSITIVE, exact=False, request=request)], request)
    assert len(enriched.context_catalog) == 1
    assert enriched.context_catalog[0].memory_comparison.use_mode.value == "context_only"
    assert enriched.memory_context_exclusions == []


def test_opposing_exact_answers_are_not_resolved_by_ranking():
    request = _request()
    records = [
        _record("MEM-PARTIAL", Verdict.FALSE_POSITIVE, exact=False, request=request),
        _record("MEM-EXACT-BENIGN", Verdict.FALSE_POSITIVE, exact=True, request=request),
        _record("MEM-EXACT-RISK", Verdict.TRUE_POSITIVE, exact=True, request=request),
    ]
    enriched = _enrich(records, request)
    assert len(enriched.context_catalog) == 3
    assert enriched.memory_context_exclusions == []


@pytest.mark.parametrize("unavailable", ["disabled", "expired", "excluded"])
def test_ineligible_exact_memory_cannot_suppress_partial(unavailable):
    request = _request()
    exact = _record("MEM-EXACT", Verdict.TRUE_POSITIVE, exact=True, request=request)
    if unavailable == "disabled":
        exact.retrieval_enabled = False
    elif unavailable == "expired":
        exact.retrieval_valid_until = datetime.now(UTC) - timedelta(seconds=1)
    else:
        exact.applicability.excluded_facets = {"environment": ["dev"]}
    enriched = _enrich([exact, _record("MEM-PARTIAL", Verdict.FALSE_POSITIVE, exact=False, request=request)], request)
    assert [item.source_id for item in enriched.context_catalog] == ["MEM-PARTIAL@v1"]
    assert enriched.memory_context_exclusions == []


def test_ruleless_canonical_scenario_can_select_exact_experience():
    request = _request().model_copy(update={"detection": DetectionRuleRef()})
    request.fact_reconstruction.scenario_hypotheses = [ScenarioHypothesis(scenario_type="proxy_tunnel_activity", confidence=0.9, rationale="Synthetic canonical scenario")]
    enriched = _enrich(
        [_record("MEM-PARTIAL", Verdict.FALSE_POSITIVE, exact=False, request=request), _record("MEM-EXACT", Verdict.TRUE_POSITIVE, exact=True, request=request)],
        request,
    )
    assert [item.source_id for item in enriched.context_catalog] == ["MEM-EXACT@v1"]
    assert "detection_key" not in enriched.memory_context_exclusions[0].memory_comparison.matched_required_facets


def test_exact_experience_for_another_detection_does_not_suppress_partial():
    request = _request()
    other = request.model_copy(update={"detection": DetectionRuleRef(detection_key="other:rule")})
    partial_items = _enrich([_record("MEM-PARTIAL", Verdict.FALSE_POSITIVE, exact=False, request=request)], request).context_catalog
    exact_items = _enrich([_record("MEM-OTHER", Verdict.TRUE_POSITIVE, exact=True, request=other)], other).context_catalog
    selected, exclusions = _select_memory_reasoning_context([*partial_items, *exact_items])
    assert len(selected) == 2
    assert exclusions == []


def test_legacy_unscoped_memory_cannot_suppress_partial():
    request = _request()
    legacy = _record("MEM-LEGACY", Verdict.TRUE_POSITIVE, exact=True, request=request)
    legacy.applicability = None
    enriched = _enrich([legacy, _record("MEM-PARTIAL", Verdict.FALSE_POSITIVE, exact=False, request=request)], request)
    assert len(enriched.context_catalog) == 2
    assert enriched.memory_context_exclusions == []


def test_selection_uses_full_matching_values_not_truncated_display():
    request = _request()
    partial = _enrich([_record("MEM-PARTIAL", Verdict.FALSE_POSITIVE, exact=False, request=request)], request).context_catalog[0]
    exact = _enrich([_record("MEM-EXACT", Verdict.TRUE_POSITIVE, exact=True, request=request)], request).context_catalog[0]
    prefix = "x" * 256
    for item, suffix in ((partial, "-scope-a"), (exact, "-scope-b")):
        item.memory_comparison.matched_required_facets["detection_key"] = [prefix]
        item.metadata["applicability_report"]["matched_required_facets"]["detection_key"] = [prefix + suffix]
    selected, exclusions = _select_memory_reasoning_context([partial, exact])
    assert len(selected) == 2
    assert exclusions == []
