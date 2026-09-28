"""Stable observed HTTP behavior, grouped within its verified transaction.

Only the normalization merger's typed, source-bound facts are consumed. Prose,
addresses, timestamps, IDs and evidence positions never enter matching values.
"""

import re
from collections import defaultdict

from soc_agent.contracts import LLMAnalysisRequest

_KINDS = {
    "http_request_directory_traversal": ("request", "directory_traversal"),
    "http_request_command_execution": ("request", "command_execution"),
    "http_request_file_upload": ("request", "file_upload"),
    "http_response_directory_listing": ("response", "directory_listing"),
    "http_response_command_output": ("response", "command_output"),
    "http_response_file_content": ("response", "file_content"),
}
_PRODUCTS = frozenset({"simplehttp", "apache", "nginx", "iis", "tomcat", "jetty", "envoy", "gunicorn", "uvicorn"})


def _belongs(path: str, source: str) -> bool:
    return path == source or path.startswith(source + "#") or path.startswith(source + ".")


def http_behavior_components(request: LLMAnalysisRequest) -> tuple[list[str], list[str], list[str]]:
    """One indivisible component per HTTP transaction; no global fact joins."""
    http = request.canonical_entities.http
    groups = defaultdict(list)
    gaps = []
    for fact in request.canonical_entities.supplementary_facts:
        descriptor = getattr(fact, "network_behavior", None)
        if descriptor is None or getattr(fact, "network_behavior_verification", None) != "source_bound_v1":
            continue
        source, ref = fact.event_scope_id, fact.subject_ref or ""
        siblings = [i for i, item in enumerate(http.observations) if _belongs(item.evidence_path, source)]
        match = re.fullmatch(r"entities\.http\.observations\[(\d+)\]", ref)
        if match and siblings == [int(match[1])]:
            binding = int(match[1])
        elif ref == "entities.http" and not http.observations:
            binding = None
        elif ref == "entities.http" and len(siblings) == 1 and len(http.observations) == 1:
            binding = siblings[0]
        else:
            gaps.append("http_behavior_subject_not_resolved:" + fact.observation_id)
            continue
        if not _belongs(fact.evidence_path, source):
            gaps.append("http_behavior_source_mismatch:" + fact.observation_id)
            continue
        groups[(source, binding)].append(descriptor)

    components, strong = set(), set()
    for descriptors in groups.values():
        request_kinds, response_kinds, products = set(), set(), set()
        for descriptor in descriptors:
            if descriptor.kind == "http_response_server_banner" and descriptor.server_product in _PRODUCTS:
                products.add(descriptor.server_product)
            elif descriptor.kind in _KINDS:
                direction, value = _KINDS[descriptor.kind]
                (request_kinds if direction == "request" else response_kinds).add(value)
        if len(products) > 1:
            gaps.append("http_behavior_server_product_ambiguous")
            continue
        parts = []
        for label, values in (("request", request_kinds), ("response", response_kinds), ("server", products)):
            if values:
                parts.append(label + "=" + ",".join(sorted(values)))
        if not parts:
            continue
        # Every value above comes from a closed vocabulary. The source/subject
        # grouping keys deliberately do not become part of the component.
        component = "http_observation:" + ";".join(parts)
        components.add(component)
        if request_kinds or response_kinds:
            strong.add(component)
    return sorted(components), sorted(strong), sorted(set(gaps))
