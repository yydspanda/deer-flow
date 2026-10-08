"""Feature accounting is frozen for audit and excluded from model evidence."""

import pytest
from test_soc_memory_governance import services as services

from soc_agent.contracts import LLMAnalysisRequest
from soc_agent.memory.profiles import GenericSocMemoryProfile
from soc_agent.memory.projection_audit import bind_memory_profile
from soc_agent.pipeline.analysis_context import project_analysis_context


class ProfileWithUsage(GenericSocMemoryProfile):
    def projection_usage(self, request):
        return [{"canonical_path": "entities.process", "status": "gap", "components": [], "reason": "subject_not_resolved"}]

    def projection_gaps(self, request):
        return ["subject_not_resolved"]


def test_consumption_is_persistable_but_not_an_extra_model_fact_or_matching_input():
    request = LLMAnalysisRequest(alert_id="synthetic")
    profile = ProfileWithUsage()
    before = profile.project_query_facets(request)
    updated = bind_memory_profile(request, profile)
    assert request.memory_projection_audit is None
    assert updated.memory_projection_audit.usages[0].status == "gap"
    assert updated.memory_projection_audit.projection_gaps == ["subject_not_resolved"]
    assert profile.project_query_facets(updated) == before
    assert "memory_projection_audit" not in project_analysis_context(updated)
    assert LLMAnalysisRequest.model_validate_json(updated.model_dump_json()).memory_projection_audit == updated.memory_projection_audit


def test_profiles_without_usage_keep_legacy_request_serialization():
    updated = bind_memory_profile(LLMAnalysisRequest(alert_id="synthetic"), GenericSocMemoryProfile())
    assert "memory_projection_audit" not in updated.model_dump(mode="json")


def test_bounded_consumption_report_explicitly_records_omissions():
    class LargeProfile(ProfileWithUsage):
        def projection_usage(self, request):
            return super().projection_usage(request) * 2001

    updated = bind_memory_profile(LLMAnalysisRequest(alert_id="synthetic"), LargeProfile())
    assert len(updated.memory_projection_audit.usages) == 2000
    assert updated.memory_projection_audit.omitted_usage_count == 1


@pytest.mark.parametrize("direct_path", [False, True])
@pytest.mark.parametrize("retrieval_fails", [False, True])
def test_both_runtime_paths_journal_the_audit_and_keep_it_after_retrieval_failure(direct_path, retrieval_fails):
    from soc_agent.contracts import AlertInput, AnalysisRun, SocMemoryRetrievalResult
    from soc_agent.core.direct_resolution import SocDirectResolutionService
    from soc_agent.core.runtime import analyze_alert
    from soc_agent.memory.profiles import SocMemoryProfileRegistry
    from soc_agent.memory.retrieval import ConfirmedMemoryAnalysisRequestEnricher

    class Memory:
        def __init__(self):
            self.queries = []

        def find_relevant_records(self, query):
            self.queries.append(query)
            if retrieval_fails:
                raise RuntimeError("synthetic offline retrieval error")
            return SocMemoryRetrievalResult(query=query)

        find_directive_records = find_relevant_records

    memory = Memory()
    registry = SocMemoryProfileRegistry(fallback=ProfileWithUsage())
    journals = []

    def journal(run, request, invocation):
        assert run.llm_analysis_request.memory_projection_audit == request.memory_projection_audit
        journals.append(run.model_dump_json())

    kwargs = {"direct_resolution": SocDirectResolutionService(memory_service=memory, profile_registry=registry)} if direct_path else {"analysis_request_enricher": ConfirmedMemoryAnalysisRequestEnricher(memory, profile_registry=registry)}
    run = analyze_alert(AlertInput(alert_id="projection-audit-runtime").model_dump(mode="json"), before_provider=journal, **kwargs)
    assert run.failure is None
    assert memory.queries
    assert journals
    persisted = AnalysisRun.model_validate_json(journals[-1])
    audit = persisted.llm_analysis_request.memory_projection_audit
    assert audit is not None
    assert audit.profile == run.llm_analysis_request.memory_profile
    assert audit.usages[0].reason == "subject_not_resolved"
    assert run.llm_analysis_request.memory_projection_audit == audit


def test_audit_details_cannot_change_prompt_catalog_or_memory_query():
    from soc_agent.contracts.normalization import FactConsumerUsage
    from soc_agent.memory.retrieval import memory_query_from_analysis_request
    from soc_agent.pipeline.reference_catalog import finalize_analysis_reference_catalogs
    from soc_agent.prompts.analysis import build_analysis_prompt

    profile = ProfileWithUsage()
    baseline = bind_memory_profile(LLMAnalysisRequest(alert_id="synthetic"), profile)
    changed = baseline.model_copy(deep=True)
    changed.memory_projection_audit.usages.append(FactConsumerUsage(canonical_path="entities.process", status="used", components=["audit-only-untrusted-component"], reason="AUDIT_ONLY_SENTINEL"))
    changed.memory_projection_audit.projection_gaps = ["audit-only-untrusted-gap"]
    assert memory_query_from_analysis_request(changed, profile=profile) == memory_query_from_analysis_request(baseline, profile=profile)
    base_catalog = finalize_analysis_reference_catalogs(baseline)
    changed_catalog = finalize_analysis_reference_catalogs(changed)
    assert base_catalog.evidence_catalog == changed_catalog.evidence_catalog
    assert build_analysis_prompt(base_catalog).messages() == build_analysis_prompt(changed_catalog).messages()


def test_rebinding_profile_without_usage_clears_stale_audit_without_mutating_saved_input():
    previous = bind_memory_profile(LLMAnalysisRequest(alert_id="synthetic"), ProfileWithUsage())
    updated = bind_memory_profile(previous, GenericSocMemoryProfile())
    assert updated.memory_projection_audit is None
    assert previous.memory_projection_audit is not None


def test_successful_direct_memory_path_persists_audit_without_running_analyzer(services, monkeypatch):
    from test_soc_direct_resolution import ForbiddenModel, memory_case

    from soc_agent.contracts import AnalysisRun
    from soc_agent.core.runtime import analyze_alert
    from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile

    def audit_usage(self, request):
        return [{"canonical_path": "entities.process", "status": "context_only", "components": [], "reason": "direct-path-audit-sentinel"}]

    payload, resolver, record = memory_case(services)
    monkeypatch.setattr(PingAnSocMemoryProfile, "projection_usage", audit_usage)
    run = analyze_alert(payload, analyzer=ForbiddenModel(), direct_resolution=resolver)
    assert run.failure is None
    assert run.direct_resolution.source_kind == "memory"
    assert run.direct_resolution.source_id == record.memory_id
    assert run.analysis is None
    services[1].save_run(run)
    persisted = services[1].get_run(run.run_id)
    assert persisted.llm_analysis_request.memory_projection_audit.usages[0].reason == "direct-path-audit-sentinel"
    assert AnalysisRun.model_validate_json(persisted.model_dump_json()).llm_analysis_request.memory_projection_audit == run.llm_analysis_request.memory_projection_audit
