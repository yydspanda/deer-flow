"""Project canonical object lineage for joint reuse-limit evaluation."""

from soc_agent.contracts.schemas import LLMAnalysisRequest, SocMemoryScopeBinding


def scope_bindings(request: LLMAnalysisRequest) -> list[SocMemoryScopeBinding]:
    network = request.canonical_entities.network
    result = []
    for index, item in enumerate(network.observations or [network]):
        roles = [f"{role}:{value}".casefold() for role, value in (("source", item.source_ip), ("destination", item.destination_ip)) if value]
        if not roles and not item.domain:
            continue
        entities = ["ip:" + value.split(":", 1)[1] for value in roles]
        if item.domain:
            entities.append("domain:" + item.domain.casefold())
        result.append(SocMemoryScopeBinding(source_ref=f"entities.network.observations[{index}]" if network.observations else "entities.network", facets={"role_entity": roles, "entity": entities}))
    process = request.canonical_entities.process
    for index, observation in enumerate(process.observations):
        for node_index, node in enumerate(observation.nodes):
            entities = []
            if observation.host_name:
                entities.append("host:" + observation.host_name.casefold())
            if node.username:
                entities.extend(["user:" + node.username.casefold(), "account:" + node.username.casefold()])
            if entities:
                result.append(SocMemoryScopeBinding(source_ref=f"entities.process.observations[{index}].nodes[{node_index}]", facets={"entity": entities}))
    host = request.canonical_entities.host
    user = request.canonical_entities.user
    if not process.observations:
        entities = [f"{kind}:{value}".casefold() for kind, value in (("host", host.host_name), ("user", user.username), ("account", user.um_account or user.username)) if value]
        if entities:
            result.append(SocMemoryScopeBinding(source_ref="entities.endpoint", facets={"entity": entities}))
    return result


def uncovered_binding_conditions(spec, query):
    conditions = [c for c in spec.reuse_conditions if (c.facet_key == "role_entity" and c.value_prefix in {"source", "destination"}) or (c.facet_key == "entity" and c.value_prefix in {"ip", "domain", "host", "user", "account"})]
    if not conditions or spec.covered_behavior_components is None:
        return False
    relevant = [binding for binding in query.scope_bindings if any(any(v.startswith(c.value_prefix + ":") for v in binding.facets.get(c.facet_key, [])) for c in conditions)]
    if not relevant:
        return True
    # A match describes the whole alert. Every relevant connection must satisfy
    # all selected role conditions together, not merely one endpoint somewhere.
    return any(any(not {v.casefold() for v in condition.values}.intersection(v.casefold() for v in binding.facets.get(condition.facet_key, [])) for condition in conditions) for binding in relevant)
