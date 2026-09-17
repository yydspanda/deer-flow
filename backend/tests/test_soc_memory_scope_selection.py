"""Scope precedence is independent of directive permission and shortlist order."""

import pytest
from test_soc_direct_resolution import memory_case
from test_soc_memory_governance import services as _services

from soc_agent.contracts import SocMemoryQuery
from soc_agent.contracts.schemas import SocMemoryReuseCondition, SocMemoryScopeBinding

services = _services


@pytest.mark.parametrize("active", [True, False])
def test_specific_reference_or_paused_scope_blocks_broad_directive(services, active):
    service, repository = services
    _, _, broad = memory_case(services)
    narrow = broad.model_copy(
        update={
            "memory_id": "MEM-SPECIFIC",
            "source_candidate_id": "MC-SPECIFIC",
            "decision_directive": None,
            "retrieval_enabled": active,
            "applicability": broad.applicability.model_copy(update={"reuse_conditions": [SocMemoryReuseCondition(facet_key="entity", value_prefix="host", values=["host:endpoint-1"])]}),
        }
    )
    repository.save_memory_record(narrow)
    request = SocMemoryQuery(
        tenant_id=broad.tenant_id,
        tenant_scope=broad.tenant_scope,
        facets={**broad.facets, "entity": ["host:endpoint-1"]},
        scope_bindings=[SocMemoryScopeBinding(source_ref="endpoint", facets={"entity": ["host:endpoint-1"]})],
        metadata={
            "memory_profile_id": broad.applicability.profile_id,
            "memory_profile_version": broad.applicability.profile_version,
            "memory_feature_schema_version": broad.applicability.feature_schema_version,
        },
    )
    for result in (service.find_directive_records(request), service.find_relevant_records(request)):
        parent = next(m for m in result.matches if m.memory_id == broad.memory_id)
        assert parent.applicability_report.status.value == "partial"
        assert narrow.memory_id in parent.applicability_report.preferred_memory_ids
        if not active:
            assert all(m.memory_id != narrow.memory_id for m in result.matches)


def test_new_service_cannot_be_applied_before_or_after_primary_analysis(services):
    service, _ = services
    _, _, record = memory_case(services)
    request = SocMemoryQuery(
        tenant_id=record.tenant_id,
        tenant_scope=record.tenant_scope,
        facets={**record.facets, "behavior_component_core": [*record.facets["behavior_component_core"], "network_service:udp/5555"]},
        metadata={
            "memory_profile_id": record.applicability.profile_id,
            "memory_profile_version": record.applicability.profile_version,
            "memory_feature_schema_version": record.applicability.feature_schema_version,
        },
    )
    for result in (service.find_directive_records(request), service.find_relevant_records(request)):
        match = next(m for m in result.matches if m.memory_id == record.memory_id)
        assert match.applicability_report.status.value == "partial"
        assert match.applicability_report.uncovered_behavior_components == ["network_service:udp/5555"]
