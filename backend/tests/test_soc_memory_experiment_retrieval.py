from test_soc_memory_retrieval_v2 import _record

from soc_agent.contracts import SocMemoryQuery
from soc_agent.core.service import SocMemoryService
from soc_agent.memory import InMemoryMemoryCandidateRepository


def test_round_allowlist_applies_to_both_context_and_direct_lookup_without_changing_authority():
    repository = InMemoryMemoryCandidateRepository()
    facets = {"detection_key": ["test-rule"], "environment": ["dev"]}
    first = _record("MEM-FIRST", facets=facets)
    other = _record("MEM-OTHER", facets=facets)
    repository.save_memory_record(first)
    repository.save_memory_record(other)
    query = SocMemoryQuery(tenant_scope="pingan", tenant_id="pingan", facets=facets)
    baseline = SocMemoryService(record_repository=repository)
    assert len(baseline.find_relevant_records(query).matches) == 2
    learning = SocMemoryService(record_repository=repository, retrieval_record_ids=frozenset())
    assert not learning.find_relevant_records(query).matches
    assert not learning.find_directive_records(query).matches
    validation = SocMemoryService(record_repository=repository, retrieval_record_ids=frozenset({first.memory_id}))
    assert [m.memory_id for m in validation.find_relevant_records(query).matches] == [first.memory_id]
    assert [m.memory_id for m in validation.find_directive_records(query).matches] == [first.memory_id]
    repository.save_memory_record(first.model_copy(update={"retrieval_enabled": False}))
    assert not validation.find_relevant_records(query).matches
    assert not validation.find_directive_records(query).matches
    assert len(baseline.find_relevant_records(query).matches) == 1


def test_empty_allowlist_survives_transactional_governance_clone():
    repository = InMemoryMemoryCandidateRepository()
    service = SocMemoryService(record_repository=repository, retrieval_record_ids=frozenset())
    clone = service._governance_clone(repository, service._event_sink)
    assert clone._retrieval_record_ids == frozenset()
