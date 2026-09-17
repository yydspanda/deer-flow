from types import SimpleNamespace

import pytest
from test_soc_memory_coverage import scope

from soc_agent.memory.scope_options import candidate_with_scope_selections, option_page


def data():
    observations = {
        str(n): SimpleNamespace(
            tenant_id="tenant",
            profile_id="generic.soc",
            profile_version="1",
            feature_schema_version="test.v1",
            environment="dev",
            source=SimpleNamespace(source_id=str(n), alert_id=str(n), run_id=str(n)),
            signature=SimpleNamespace(facets={"role_entity": [f"destination:192.0.2.{n}"]}),
        )
        for n in range(1, 51)
    }
    candidate = SimpleNamespace(
        candidate_id="candidate", tenant_id="tenant", applicability=scope(), metadata={"observation_ids": list(observations)}, source=SimpleNamespace(metadata={}, alert_id="45", source_id="45", run_id="45"), facets={}
    )
    return candidate, SimpleNamespace(get_memory_pattern_observation=observations.get)


def test_source_directory_is_paged_and_prioritizes_current_alert():
    candidate, repository = data()
    groups = option_page(candidate, repository)
    assert groups["items"] == []
    assert groups["groups"] == [{"facet_key": "role_entity", "value_prefix": "destination"}]
    page = option_page(candidate, repository, facet_key="role_entity", prefix="destination", limit=5)
    assert page["total"] == 50
    assert len(page["items"]) == 5
    assert page["items"][0]["value"] == "destination:192.0.2.45"
    assert page["items"][0]["sample_count"] == 1
    found = option_page(candidate, repository, facet_key="role_entity", prefix="destination", search="192.0.2.39")
    assert found["total"] == 1


def test_unknown_entity_cannot_be_added_by_forging_request():
    candidate, repository = data()
    with pytest.raises(ValueError, match="来源样本"):
        candidate_with_scope_selections(candidate, repository, {"role_entity": ["destination:203.0.113.99"]})
