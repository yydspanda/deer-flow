"""Prove an observed HTTP responder service without inventing wire direction.

Only canonical values, their provenance and adapter-owned semantic types participate.
This opt-in projector does not change historical Profiles or canonical observations.
"""

from collections import defaultdict

from soc_agent.contracts import CanonicalFieldProvenance, LLMAnalysisRequest, SourceFieldSemantic

_FIELDS = ("source_ip", "destination_ip", "src_port", "dst_port", "protocol")
_ROLES = frozenset({"provider_reported_session_initiator", "provider_reported_session_responder"})
_HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH"})


def _source(path: str) -> str:
    return path.partition("#")[0]


def _raw_path(path: str, source: str) -> str | None:
    """Compare losslessly decoded fields with their adapter content-role owner."""
    for projection in ("parsed", "decoded", "repaired"):
        prefix = source + "#" + projection + "."
        if path.startswith(prefix):
            return source + "." + path[len(prefix) :]
    return path if path.startswith(source + ".") and "#" not in path else None


def _related(left: str, right: str) -> bool:
    return left == right or any(left.startswith(right + separator) or right.startswith(left + separator) for separator in (".", "[", "#"))


def _field_proof(provenance, ref: str, field: str, value, source: str) -> CanonicalFieldProvenance | None:
    entries = provenance.get(ref + "." + field, [])
    if value is None or len(entries) != 1:
        return None
    entry = entries[0]
    if entry.selected_value != str(value) or entry.source_layer not in {"raw_message", "raw_structured"} or entry.trust_level != "high" or entry.alternative_values or _raw_path(entry.selected_from, source) is None:
        return None
    return entry


def _session_role(proof: CanonicalFieldProvenance, semantics: list[SourceFieldSemantic]) -> str | None:
    roles = {item.semantic_type for item in semantics if item.field_path == proof.selected_from and item.semantic_type in _ROLES and item.participates_in_entities and item.participates_in_reasoning}
    return next(iter(roles)) if len(roles) == 1 else None


def _has_http_role(proof: CanonicalFieldProvenance, source: str, semantics: list[SourceFieldSemantic], roles: set[str]) -> bool:
    selected = _raw_path(proof.selected_from, source)
    for item in semantics:
        if not item.participates_in_reasoning or item.semantic_type.casefold() not in roles:
            continue
        owner = _raw_path(item.field_path, source)
        if owner and selected and (selected == owner or selected.startswith(owner + ".")):
            return True
    return False


def _http_proofs(observation, ref: str, source: str, provenance, semantics):
    proofs = []
    method = _field_proof(provenance, ref, "method", observation.method, source)
    if method and observation.method in _HTTP_METHODS and _has_http_role(method, source, semantics, {"observed_http_request_headers", "observed_http_request_method"}):
        proofs.append(method)
    status = _field_proof(provenance, ref, "status_code", observation.status_code, source)
    if status and 100 <= observation.status_code <= 599 and _has_http_role(status, source, semantics, {"observed_http_response_headers", "observed_http_response_status"}):
        proofs.append(status)
    return proofs


def _has_conflict(request: LLMAnalysisRequest, refs: tuple[str, ...], proofs: list[CanonicalFieldProvenance]) -> bool:
    def normalized(path):
        if path.partition("#")[2] in {"parsed", "decoded", "repaired"}:
            return _source(path)
        return _raw_path(path, _source(path)) or path

    paths = [*refs, *(normalized(item.selected_from) for item in proofs)]
    for conflict in request.fact_reconstruction.conflict_reports:
        # An unscoped conflict cannot be shown unrelated to this observation.
        # candidate_values often uses role names such as "source", not paths.
        if not conflict.involved_fields:
            return True
        involved = [*conflict.involved_fields, *(key for key in conflict.candidate_values if key.startswith("entities.") or "#" in key)]
        if any(_related(normalized(field), path) for field in involved for path in paths):
            return True
    return False


def proven_application_services(request: LLMAnalysisRequest) -> dict[str, str]:
    """Return only uniquely bound, directly evidenced HTTP responder services.

    A responder is the provider-reported endpoint of this observed session, not
    a claim about the ultimate backend, the transport or attacker/victim roles.
    Explicit network references remain independent; an aggregate needs one
    observation and matching provenance for every field of its own summary.
    """
    network = request.canonical_entities.network
    http = request.canonical_entities.http
    if http.x_forwarded_for:
        return {}
    semantics = request.source_field_semantics
    provenance = defaultdict(list)
    for entry in request.fact_reconstruction.canonical_field_provenance:
        provenance[entry.canonical_path].append(entry)
    networks_by_source = defaultdict(list)
    for index, observation in enumerate(network.observations):
        networks_by_source[_source(observation.evidence_path)].append(index)
    http_by_source = defaultdict(list)
    for index, observation in enumerate(http.observations):
        http_by_source[_source(observation.evidence_path)].append(index)

    services = {}
    for index, observation in enumerate(network.observations):
        ref = f"entities.network.observations[{index}]"
        source = _source(observation.evidence_path)
        protocol = (observation.protocol or "").strip().casefold()
        if protocol not in {"http", "https"} or observation.forwarded_chain or len(networks_by_source[source]) != 1 or len(http_by_source[source]) != 1:
            continue
        http_index = http_by_source[source][0]
        http_observation = http.observations[http_index]
        http_ref = f"entities.http.observations[{http_index}]"
        if http_observation.x_forwarded_for:
            continue
        fields = {field: _field_proof(provenance, ref, field, getattr(observation, field), source) for field in _FIELDS}
        if not all(fields.values()) or not observation.source_ip or not observation.destination_ip:
            continue
        if any(port is None or not 1 <= port <= 65535 for port in (observation.src_port, observation.dst_port)):
            continue
        source_role = _session_role(fields["source_ip"], semantics)
        destination_role = _session_role(fields["destination_ip"], semantics)
        if {source_role, destination_role} != _ROLES:
            continue
        responder_is_source = source_role == "provider_reported_session_responder"
        direction = (observation.direction or "").strip().casefold()
        if direction and direction != ("to_client" if responder_is_source else "to_server"):
            continue
        http_proofs = _http_proofs(http_observation, http_ref, source, provenance, semantics)
        proofs = [*fields.values(), *http_proofs]
        if not http_proofs or _has_conflict(request, (ref, http_ref), proofs):
            continue
        service = f"{protocol}/{observation.src_port if responder_is_source else observation.dst_port}"
        services[ref] = service

        if len(network.observations) != 1 or (network.direction or "").strip().casefold() not in {"", direction}:
            continue
        aggregate_proofs = {field: _field_proof(provenance, "entities.network", field, getattr(network, field), source) for field in _FIELDS}
        if all(aggregate_proofs.values()) and all(getattr(network, field) == getattr(observation, field) and aggregate_proofs[field].selected_from == fields[field].selected_from for field in _FIELDS):
            if not _has_conflict(request, ("entities.network", http_ref), [*aggregate_proofs.values(), *http_proofs]):
                services["entities.network"] = service
    return services
