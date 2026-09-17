"""Verify PingAn fingerprint ingredients before exposing a readable scope."""

from soc_agent.contracts import SocMemoryApplicabilitySpec
from soc_agent.utils.hashing import rule_entity_key, stable_hash


def explain_scope_facets(spec: SocMemoryApplicabilitySpec, facets: dict[str, list[str]]) -> dict[str, dict[str, list[str]]]:
    result: dict[str, dict[str, list[str]]] = {}
    required = spec.required_facets
    signature_keys = ("source_system", "product", "rule_name")
    if all(len(facets.get(key, [])) == 1 for key in signature_keys):
        signature = stable_hash({"schema_version": "pingan.soc.detection_signature.v1", **{key: " ".join(facets[key][0].split()).casefold() for key in signature_keys}})
        if required.get("detection_signature") == [signature]:
            result["detection_signature"] = {key: facets[key] for key in signature_keys}
            result["detection_signature"]["entity"] = [f"rule_name:{facets['rule_name'][0]}"]
    if len(facets.get("rule_code", [])) == 1 and len(facets.get("source_system", [])) == 1:
        code, source = facets["rule_code"][0], facets["source_system"][0]
        if required.get("detection_key") == [f"{source}:rule_code:{code}".casefold()]:
            result["detection_key"] = {"rule_code": [code], "source_system": [source], "entity": [f"rule_code:{code}", rule_entity_key(required["detection_key"][0])]}
    components = sorted(set(facets.get("behavior_component_core") or facets.get("behavior_component", [])))
    version = {"pingan.soc.memory_features.v5": "v5", "pingan.soc.memory_features.v6": "v6", "pingan.soc.memory_features.v7": "v7"}.get(spec.feature_schema_version)
    if version is None:
        return result
    fingerprint = stable_hash({"schema_version": f"pingan.soc.memory_behavior_fingerprint.{version}", "components": components})
    if components and required.get("behavior_fingerprint") == [fingerprint]:
        expansion = {"behavior_component": components}
        for key in ("behavior_component_core", "behavior_component_weak"):
            expansion[key] = [value for value in facets.get(key, []) if value in components]
        for key, prefix in (("scenario_key", "scenario:"), ("network_service", "network_service:"), ("vulnerability_id", "vulnerability:"), ("attack_behavior_family", "attack_family:")):
            values = [value.removeprefix(prefix) for value in components if value.startswith(prefix)]
            if values:
                expansion[key] = values
        expansion["entity"] = [f"mitre:{value.removeprefix('technique:')}" for value in components if value.startswith("technique:")]
        result["behavior_fingerprint"] = expansion
    return result
