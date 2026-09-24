"""Read-only scope explanations must not invent fingerprint contents or authority."""

from copy import deepcopy

from test_soc_memory_retrieval_v2 import _record

from soc_agent.contracts import AlertInput, DetectionRuleRef, EntityKind, SocMemoryApplicabilitySpec, SocMemoryQuery
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.memory.scope_view import build_memory_scope_view
from soc_agent.memory.scoring import evaluate_memory_applicability, score_memory_record
from soc_agent.pipeline.extractor import extract_entities
from soc_agent.utils.hashing import stable_hash


def sample():
    facets = {
        "source_type": ["ndr"],
        "source_system": ["sec_guard_apt"],
        "product": ["360天眼APT"],
        "rule_name": ["红队IP监控"],
        "rule_code": ["RPAADM_000558"],
        "scenario_key": ["web_attack"],
        "behavior_component": ["attack_family:denial_of_service", "network_service:http/8080", "protocol:http", "scenario:web_attack", "technique:t1190"],
        "behavior_component_strong": ["technique:t1190"],
        "entity": ["asset:未知资产组", "mitre:TA0001", "rule_code:RPAADM_000558"],
    }
    spec = SocMemoryApplicabilitySpec(
        profile_id="pingan.soc",
        profile_version="7",
        feature_schema_version="pingan.soc.memory_features.v5",
        required_facets={
            "detection_key": ["sec_guard_apt:rule_code:rpaadm_000558"],
            "detection_signature": ["cfcf75a28055fdc3f5e0306ad86b4da136bd704d77462aa30d0771df71bf671f"],
            "behavior_fingerprint": ["3cd30c2a0971d87700bb21d569ac3333cf033eefd0bf3f7429666a8c8aa791fd"],
            "behavior_strength": ["strong"],
            "environment": ["dev-corpus-eval"],
        },
        optional_facets={key: values for key, values in facets.items() if key not in {"rule_name", "rule_code"}},
        context_only_required_facet_keys=["detection_key", "detection_signature", "behavior_strength", "environment"],
        context_only_missing_facet_keys=["behavior_fingerprint"],
        context_only_similarity_facet_keys=["behavior_component_strong"],
    )
    return spec, facets


def project(spec, facets):
    return build_memory_scope_view(spec, facets, registry=SocMemoryProfileRegistry([PingAnSocMemoryProfile()]))


def test_verified_fingerprint_expansion_and_redundant_options_are_read_only():
    spec, facets = sample()
    before = deepcopy((spec.model_dump(), facets))
    view = project(spec, facets)
    assert view.required_details["detection_signature"]["rule_name"] == ["红队IP监控"]
    assert view.required_details["behavior_fingerprint"]["behavior_component"] == facets["behavior_component"]
    assert [option.key for option in view.options if option.kind == "additional"] == ["source_type", "entity"]
    entity = next(option for option in view.options if option.key == "entity")
    assert entity.values == ["asset:未知资产组", "mitre:TA0001"]
    assert entity.covered_values == ["rule_code:RPAADM_000558"]
    assert next(option for option in view.options if option.key == "behavior_component_strong").kind == "similarity"
    assert (spec.model_dump(), facets) == before


def test_damaged_or_unknown_fingerprint_is_not_explained_as_verified():
    spec, facets = sample()
    facets["behavior_component"].append("service_uri:/new")
    facets["product"] = ["other product"]
    view = project(spec, facets)
    assert "behavior_fingerprint" not in view.required_details
    assert "detection_signature" not in view.required_details
    assert "behavior_fingerprint" in view.unresolved_fingerprint_keys
    assert next(option for option in view.options if option.key == "product").kind == "additional"
    unknown = spec.model_copy(update={"profile_id": "another.vendor"})
    assert not project(unknown, facets).required_details
    old = spec.model_copy(update={"profile_version": "1"})
    assert not project(old, facets).required_details


def test_multi_value_group_keeps_independent_values_available():
    spec, facets = sample()
    spec.optional_facets["entity"] = ["ip:10.0.0.1", "asset:unknown"]
    option = next(option for option in project(spec, facets).options if option.key == "entity")
    assert option.kind == "additional"
    assert option.values == ["ip:10.0.0.1", "asset:unknown"]


def test_missing_scope_and_existing_required_narrowing_are_preserved():
    assert build_memory_scope_view(None, {}, registry=SocMemoryProfileRegistry()) is None
    spec, facets = sample()
    spec.optional_facets.pop("entity")
    spec.optional_facets.pop("source_type")
    spec.required_facets["entity"] = ["ip:10.0.0.1"]
    spec.context_only_required_facet_keys.append("entity")
    spec.excluded_facets["source_type"] = ["hids"]
    project(spec, facets)
    assert spec.required_facets["entity"] == ["ip:10.0.0.1"]
    assert spec.excluded_facets["source_type"] == ["hids"]


def test_core_components_explain_hash_without_claiming_supplementary_values():
    spec, facets = sample()
    facets["behavior_component_core"] = list(facets["behavior_component"])
    facets["behavior_component"].append("process:extra.exe")
    view = project(spec, facets)
    assert "process:extra.exe" not in view.required_details["behavior_fingerprint"]["behavior_component"]
    spec.optional_facets["behavior_component"].append("process:extra.exe")
    option = next(option for option in project(spec, facets).options if option.key == "behavior_component")
    assert option.kind == "additional"
    assert option.values == ["process:extra.exe"]


def test_core_and_weak_aliases_are_not_offered_as_duplicate_editable_limits():
    spec, facets = sample()
    for key in ("behavior_component_core", "behavior_component_weak"):
        facets[key] = list(facets["behavior_component"])
        spec.optional_facets[key] = list(facets[key])
    view = project(spec, facets)
    assert [option.key for option in view.options if option.kind == "additional"] == ["source_type", "entity"]


def test_semantic_feature_profile_verifies_its_own_fingerprint_version():
    spec, facets = sample()
    profile = PingAnSocMemoryProfile(semantic_features=True)
    spec.profile_version = profile.identity.profile_version
    spec.feature_schema_version = profile.identity.feature_schema_version
    spec.required_facets["behavior_fingerprint"] = [stable_hash({"schema_version": "pingan.soc.memory_behavior_fingerprint.v8", "components": sorted(facets["behavior_component"])})]
    view = build_memory_scope_view(spec, facets, registry=SocMemoryProfileRegistry([profile]))
    assert view.required_details["behavior_fingerprint"]["behavior_component"] == sorted(facets["behavior_component"])
    assert not view.unresolved_fingerprint_keys


def test_rule_entity_hash_is_the_existing_detection_identity_not_new_information():
    key = "sec_guard_apt:rule_code:rpaadm_000451"
    entities = extract_entities(AlertInput(alert_id="2456233", detection=DetectionRuleRef(detection_key=key)))
    rule = next(item for item in entities.mentions if item.kind is EntityKind.RULE)
    assert rule.key == "rule:e29555055c12b7c7"
    assert rule.value == key
    assert rule.evidence_path == "detection.detection_key"


def test_saved_rule_hash_is_covered_but_other_entities_are_not_hidden():
    spec, facets = sample()
    spec.optional_facets["entity"] += ["rule:9d6ba27d4738e67e", "rule:other-rule", "ip:10.0.0.1"]
    before = deepcopy(spec.model_dump())
    option = next(item for item in project(spec, facets).options if item.key == "entity")
    assert "rule:9d6ba27d4738e67e" in option.covered_values
    assert option.values == ["asset:未知资产组", "mitre:TA0001", "rule:other-rule", "ip:10.0.0.1"]
    assert spec.model_dump() == before


def test_new_scope_omits_only_duplicate_rule_hash_and_preserves_query_features():
    spec, facets = sample()
    facets["entity"] += ["rule:9d6ba27d4738e67e", "rule:other-rule"]
    before = deepcopy(facets)
    built = PingAnSocMemoryProfile().build_applicability(consensus_facets={**facets, **spec.required_facets}, strong_anchor_facets=spec.required_facets)
    assert built is not None
    assert built.required_facets == spec.required_facets
    assert "rule:9d6ba27d4738e67e" not in built.optional_facets["entity"]
    assert "rule:other-rule" in built.optional_facets["entity"]
    assert facets == before

    record_facets = {**facets, **spec.required_facets}
    record = _record("MEM-RULE-REDUNDANCY", facets=record_facets).model_copy(update={"applicability": built})
    legacy = record.model_copy(update={"applicability": built.model_copy(update={"optional_facets": {**built.optional_facets, "entity": list(facets["entity"])}})})
    for fingerprint in (spec.required_facets["behavior_fingerprint"], ["different-behavior"]):
        query = SocMemoryQuery(
            facets={**record_facets, "behavior_fingerprint": fingerprint},
            metadata={"memory_profile_id": built.profile_id, "memory_profile_version": built.profile_version, "memory_feature_schema_version": built.feature_schema_version},
        )
        current = evaluate_memory_applicability(record, query, {})
        previous = evaluate_memory_applicability(legacy, query, {})
        assert (current.status, current.context_only_allowed) == (previous.status, previous.context_only_allowed)
        if fingerprint == spec.required_facets["behavior_fingerprint"]:
            assert current.status.value == "applicable"
        else:
            assert current.context_only_allowed
        assert score_memory_record(record, query) == score_memory_record(legacy, query)


def test_only_duplicate_entity_group_is_removed_and_multi_rule_scope_is_not_assumed():
    spec, facets = sample()
    facets["entity"] = ["rule:9d6ba27d4738e67e"]
    profile = PingAnSocMemoryProfile()
    built = profile.build_applicability(consensus_facets={**facets, **spec.required_facets}, strong_anchor_facets=spec.required_facets)
    assert "entity" not in built.optional_facets
    assert facets["entity"] == ["rule:9d6ba27d4738e67e"]
    spec.required_facets["detection_key"].append("sec_guard_apt:rule_code:another")
    built = profile.build_applicability(consensus_facets={**facets, **spec.required_facets}, strong_anchor_facets=spec.required_facets)
    assert built.optional_facets["entity"] == facets["entity"]
    spec.optional_facets["entity"] = list(facets["entity"])
    assert next(item for item in project(spec, facets).options if item.key == "entity").kind == "additional"


def test_already_required_or_unknown_profile_rule_hash_is_not_silently_removed():
    spec, facets = sample()
    spec.required_facets["entity"] = ["rule:9d6ba27d4738e67e"]
    before = deepcopy(spec.model_dump())
    project(spec, facets)
    assert spec.model_dump() == before
    for field, value in (("profile_id", "another.vendor"), ("profile_version", "1")):
        legacy = spec.model_copy(update={field: value}, deep=True)
        legacy.required_facets.pop("entity")
        legacy.optional_facets["entity"] = ["rule:9d6ba27d4738e67e"]
        option = next(item for item in project(legacy, facets).options if item.key == "entity")
        assert option.values == ["rule:9d6ba27d4738e67e"]
        assert option.kind == "additional"
