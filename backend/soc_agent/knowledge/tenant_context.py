"""Select and project relevant reviewed tenant knowledge as C-* context."""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from soc_agent.contracts import (
    AnalysisContextCatalogItem,
    AnalysisContextReferenceKind,
    LLMAnalysisRequest,
    TenantFileObservationPattern,
    TenantKnowledgeFact,
    TenantKnowledgeProfile,
    TenantProcessObservationPattern,
)
from soc_agent.utils.hashing import stable_hash


class CompositeAnalysisRequestEnricher:
    """Apply independent bounded enrichers in a stable order."""

    def __init__(self, enrichers: Iterable[Callable[[LLMAnalysisRequest], LLMAnalysisRequest]]) -> None:
        self._enrichers = tuple(enrichers)

    def __call__(self, request: LLMAnalysisRequest) -> LLMAnalysisRequest:
        for enricher in self._enrichers:
            request = enricher(request)
        return request


class TenantKnowledgeAnalysisRequestEnricher:
    """Project only profile facts relevant to the current canonical request."""

    def __init__(self, profiles: Iterable[TenantKnowledgeProfile]) -> None:
        self._profiles = tuple(profiles)

    def __call__(self, request: LLMAnalysisRequest) -> LLMAnalysisRequest:
        items: list[AnalysisContextCatalogItem] = list(request.context_catalog)
        for profile in self._profiles:
            if not _profile_applies(profile, request):
                continue
            facts = _matching_facts(profile, request)
            items.extend(_fact_context_item(profile, fact, matches) for fact, matches in facts)
        return request.model_copy(update={"context_catalog": _dedupe_context_items(items)})


def load_tenant_knowledge_profile(path: str | Path) -> TenantKnowledgeProfile:
    """Load one strictly validated profile without executing tenant code."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return TenantKnowledgeProfile.model_validate(payload)


def _profile_applies(profile: TenantKnowledgeProfile, request: LLMAnalysisRequest) -> bool:
    integration_match = bool(request.source.integration_name and request.source.integration_name in profile.integration_names)
    tenant_match = bool(request.tenant_id and request.tenant_id in profile.tenant_ids)
    return integration_match or tenant_match


def _matching_facts(
    profile: TenantKnowledgeProfile,
    request: LLMAnalysisRequest,
) -> list[tuple[TenantKnowledgeFact, dict[str, list[str]]]]:
    signals = _request_signals(request)
    matched: list[tuple[TenantKnowledgeFact, dict[str, list[str]]]] = []
    used_chars = 0
    for fact in sorted(profile.facts, key=lambda item: (item.priority, item.fact_id)):
        matches = _selector_matches(fact, signals)
        if matches is None:
            continue
        projected_chars = len(fact.label) + len(fact.statement) + sum(len(value) for values in matches.values() for value in values)
        if matched and used_chars + projected_chars > profile.max_projected_chars:
            continue
        matched.append((fact, matches))
        used_chars += projected_chars
        if len(matched) >= profile.max_projected_items:
            break
    return matched


def _request_signals(request: LLMAnalysisRequest) -> dict[str, Any]:
    ips = set(request.extracted_entities.ips)
    domains = set(request.extracted_entities.domains)
    hosts = set(request.extracted_entities.hosts)
    process_names = set(request.extracted_entities.processes)
    parent_process_names: set[str] = set()
    paths: set[str] = set()
    command_lines: set[str] = set()
    parent_command_lines: set[str] = set()
    accounts = set(request.extracted_entities.users)
    uris = set(request.extracted_entities.urls)
    process_observations: list[dict[str, Any]] = []
    file_observations: list[dict[str, str]] = []
    network = request.canonical_entities.network
    for value in (
        network.source_ip,
        network.destination_ip,
    ):
        if value:
            ips.add(value)
    for observation in network.observations:
        if observation.source_ip:
            ips.add(observation.source_ip)
        if observation.destination_ip:
            ips.add(observation.destination_ip)
        ips.update(observation.forwarded_chain)
    for value in (network.domain, network.url):
        if value:
            domains.add(value)
    if network.url:
        uris.add(network.url)

    host = request.canonical_entities.host
    if host.host_name:
        hosts.add(host.host_name)

    process = request.canonical_entities.process
    for value in (process.process_name, process.parent_process_name):
        if value:
            process_names.add(value)
    if process.parent_process_name:
        parent_process_names.add(process.parent_process_name)
    if process.process_path:
        paths.add(process.process_path)
    for value in (process.command_line, process.parent_command_line):
        if value:
            command_lines.add(value)
    if process.parent_command_line:
        parent_command_lines.add(process.parent_command_line)
    for observation in process.observations:
        observation_process_names: set[str] = set()
        observation_paths: set[str] = set()
        observation_command_lines: set[str] = set()
        node_identities: set[tuple[str, int | None]] = set()
        if observation.host_name:
            hosts.add(observation.host_name)
        for node in observation.nodes:
            process_names.add(node.process_name)
            normalized_process_name = _normalize_process_name(node.process_name)
            observation_process_names.add(normalized_process_name)
            if node.process_id is not None:
                node_identities.add((normalized_process_name, node.process_id))
            if node.process_path:
                paths.add(node.process_path)
                observation_paths.add(_normalize_path(node.process_path))
            if node.command_line:
                command_lines.add(node.command_line)
                observation_command_lines.add(_normalize_command_line(node.command_line))
            if node.username:
                accounts.add(node.username)
        process_observations.append(
            {
                "observation_id": observation.observation_id,
                "event_scope_id": observation.event_scope_id,
                "node_identities": node_identities,
                "process_names": observation_process_names,
                "paths": observation_paths,
                "command_lines": observation_command_lines,
            }
        )
    process_observations = _coalesce_process_observation_signals(process_observations)

    user = request.canonical_entities.user
    for value in (
        user.username,
        user.user_id,
        user.um_account,
        user.src_user,
        user.dst_user,
    ):
        if value:
            accounts.add(value)

    file = request.canonical_entities.file
    if file.file_path:
        paths.add(file.file_path)
    for observation in file.observations:
        if observation.file_path:
            paths.add(observation.file_path)
        file_observations.append(
            {
                "observation_id": observation.observation_id,
                "relation": observation.relation.value,
                "file_name": _normalize_file_name(observation.file_name or ""),
                "file_path": _normalize_path(observation.file_path or ""),
            }
        )

    http = request.canonical_entities.http
    for forwarded in [http.x_forwarded_for, *(item.x_forwarded_for for item in http.observations)]:
        if forwarded:
            ips.update(part.strip() for part in forwarded.split(","))
    for value in (http.host, http.url):
        if value:
            domains.add(value)
    for value in (http.path, http.url):
        if value:
            uris.add(value)
    for observation in http.observations:
        if observation.host:
            domains.add(observation.host)
        if observation.url:
            domains.add(observation.url)
        for value in (observation.path, observation.url):
            if value:
                uris.add(value)

    for value in request.extracted_entities.urls:
        domains.add(value)

    text_parts = [
        request.detection.detection_key,
        request.detection.rule_code,
        request.detection.rule_name,
        request.detection.rule_category,
        request.classification.category,
        request.classification.severity,
        *(item.scenario_type for item in request.fact_reconstruction.scenario_hypotheses),
    ]
    if request.primary_evidence is not None:
        text_parts.append(request.primary_evidence.content[:12000])
    text_parts.extend(item.content[:3000] for item in request.supplementary_evidence[:4])
    return {
        "ips": {_normalize_ip(value) for value in ips if _normalize_ip(value)},
        "domains": {_normalize_domain(value) for value in domains if _normalize_domain(value)},
        "hosts": {_normalize_host(value) for value in hosts if _normalize_host(value)},
        "process_names": {_normalize_process_name(value) for value in process_names if _normalize_process_name(value)},
        "parent_process_names": {_normalize_process_name(value) for value in parent_process_names if _normalize_process_name(value)},
        "paths": {_normalize_path(value) for value in paths if _normalize_path(value)},
        "command_lines": {_normalize_command_line(value) for value in command_lines if _normalize_command_line(value)},
        "parent_command_lines": {_normalize_command_line(value) for value in parent_command_lines if _normalize_command_line(value)},
        "process_observations": process_observations,
        "file_observations": file_observations,
        "accounts": {normalized for value in accounts if (normalized := _normalize_account(value))},
        "uris": {_normalize_uri(value) for value in uris if _normalize_uri(value)},
        "text": "\n".join(str(value) for value in text_parts if value).casefold(),
        "source_type": request.source.source_type.value,
    }


def _selector_matches(
    fact: TenantKnowledgeFact,
    signals: dict[str, Any],
) -> dict[str, list[str]] | None:
    selector = fact.selector
    matched: dict[str, list[str]] = {}
    if selector.source_types:
        values = [signals["source_type"]] if signals["source_type"] in selector.source_types else []
        if not values:
            return None
        matched["source_types"] = values
    if selector.exact_ips:
        values = sorted(signals["ips"].intersection(_normalize_ip(value) for value in selector.exact_ips))
        if not values:
            return None
        matched["exact_ips"] = values[:20]
    if selector.cidrs:
        values = _match_cidrs(signals["ips"], selector.cidrs)
        if not values:
            return None
        matched["cidrs"] = values[:20]
    if selector.domain_suffixes:
        values = sorted(domain for domain in signals["domains"] if any(_domain_matches_suffix(domain, suffix) for suffix in selector.domain_suffixes))
        if not values:
            return None
        matched["domain_suffixes"] = values[:20]
    if selector.text_terms:
        values = [term for term in selector.text_terms if term.casefold() in signals["text"]]
        if not values:
            return None
        matched["text_terms"] = values[:20]
    if selector.host_prefixes:
        prefixes = tuple(value.casefold() for value in selector.host_prefixes)
        values = sorted(value for value in signals["hosts"] if value.startswith(prefixes))
        if not values:
            return None
        matched["host_prefixes"] = values[:20]
    if selector.process_names:
        expected = {_normalize_process_name(value) for value in selector.process_names}
        values = sorted(signals["process_names"].intersection(expected))
        if not values:
            return None
        matched["process_names"] = values[:20]
    if selector.parent_process_names:
        expected = {_normalize_process_name(value) for value in selector.parent_process_names}
        values = sorted(signals["parent_process_names"].intersection(expected))
        if not values:
            return None
        matched["parent_process_names"] = values[:20]
    if selector.path_prefixes:
        prefixes = [_normalize_path(value) for value in selector.path_prefixes]
        values = sorted(value for value in signals["paths"] if any(_path_matches_prefix(value, prefix) for prefix in prefixes))
        if not values:
            return None
        matched["path_prefixes"] = values[:20]
    if selector.command_terms:
        normalized_terms = [(term, _normalize_command_line(term)) for term in selector.command_terms]
        values = [term for term, normalized in normalized_terms if normalized and any(normalized in command_line for command_line in signals["command_lines"])]
        if not values:
            return None
        matched["command_terms"] = values[:20]
    if selector.parent_command_terms:
        normalized_terms = [(term, _normalize_command_line(term)) for term in selector.parent_command_terms]
        values = [term for term, normalized in normalized_terms if normalized and any(normalized in command_line for command_line in signals["parent_command_lines"])]
        if not values:
            return None
        matched["parent_command_terms"] = values[:20]
    if selector.process_observation_patterns:
        values = _match_process_observation_patterns(
            signals["process_observations"],
            selector.process_observation_patterns,
        )
        if not values:
            return None
        matched["process_observation_patterns"] = values[:20]
    if selector.file_observation_patterns:
        values = _match_file_observation_patterns(
            signals["file_observations"],
            selector.file_observation_patterns,
        )
        if not values:
            return None
        matched["file_observation_patterns"] = values[:20]
    if selector.account_patterns:
        patterns = [re.compile(pattern, re.IGNORECASE) for pattern in selector.account_patterns]
        values = sorted(value for value in signals["accounts"] if any(pattern.fullmatch(value) for pattern in patterns))
        if not values:
            return None
        matched["account_patterns"] = values[:20]
    if selector.uri_prefixes:
        prefixes = [_normalize_uri(value) for value in selector.uri_prefixes]
        values = sorted(value for value in signals["uris"] if any(_uri_matches_prefix(value, prefix) for prefix in prefixes))
        if not values:
            return None
        matched["uri_prefixes"] = values[:20]
    return matched


def _coalesce_process_observation_signals(
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    standalone: list[dict[str, Any]] = []
    by_event_scope: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        event_scope_id = observation.get("event_scope_id")
        if event_scope_id:
            by_event_scope.setdefault(event_scope_id, []).append(observation)
        else:
            standalone.append(observation)

    result = list(standalone)
    for event_scope_id, scoped in sorted(by_event_scope.items()):
        remaining = sorted(scoped, key=lambda item: item["observation_id"])
        components: list[list[dict[str, Any]]] = []
        while remaining:
            component = [remaining.pop(0)]
            identities = set(component[0]["node_identities"])
            changed = True
            while changed:
                changed = False
                for candidate in list(remaining):
                    if identities.intersection(candidate["node_identities"]):
                        remaining.remove(candidate)
                        component.append(candidate)
                        identities.update(candidate["node_identities"])
                        changed = True
            components.append(component)

        for index, component in enumerate(components, start=1):
            component_id = event_scope_id
            if len(components) > 1:
                component_id = f"{event_scope_id}#component-{index}"
            result.append(
                {
                    "observation_id": component_id,
                    "event_scope_id": event_scope_id,
                    "node_identities": set().union(*(item["node_identities"] for item in component)),
                    "process_names": set().union(*(item["process_names"] for item in component)),
                    "paths": set().union(*(item["paths"] for item in component)),
                    "command_lines": set().union(*(item["command_lines"] for item in component)),
                }
            )
    return result


def _match_process_observation_patterns(
    observations: list[dict[str, Any]],
    patterns: list[TenantProcessObservationPattern],
) -> list[str]:
    matches: list[str] = []
    for observation in observations:
        for pattern in patterns:
            required_names = {_normalize_process_name(value) for value in pattern.required_process_names}
            if not required_names <= observation["process_names"]:
                continue
            required_name_prefixes = [_normalize_process_name(value) for value in pattern.required_process_name_prefixes]
            if not all(any(process_name.startswith(prefix) for process_name in observation["process_names"]) for prefix in required_name_prefixes):
                continue
            required_commands = [_normalize_command_line(value) for value in pattern.required_command_terms]
            if required_commands and not any(all(term in command for term in required_commands) for command in observation["command_lines"]):
                continue
            required_exact_commands = [_normalize_command_line(value) for value in pattern.required_exact_command_lines]
            if not all(command in observation["command_lines"] for command in required_exact_commands):
                continue
            required_paths = [_normalize_path(value) for value in pattern.required_path_prefixes]
            if not all(any(_path_matches_prefix(path, prefix) for path in observation["paths"]) for prefix in required_paths):
                continue
            details = [
                f"process_names={','.join(sorted(required_names))}",
            ]
            if required_name_prefixes:
                details.append(f"process_name_prefixes={','.join(required_name_prefixes)}")
            if required_commands:
                details.append(f"command_terms={','.join(required_commands)}")
            if required_exact_commands:
                details.append(f"exact_command_lines={','.join(required_exact_commands)}")
            if required_paths:
                details.append(f"path_prefixes={','.join(required_paths)}")
            matches.append(f"{observation['observation_id']}|{'|'.join(details)}")
    return list(dict.fromkeys(matches))


def _match_file_observation_patterns(
    observations: list[dict[str, str]],
    patterns: list[TenantFileObservationPattern],
) -> list[str]:
    matches: list[str] = []
    for observation in observations:
        for pattern in patterns:
            if pattern.required_relations and observation["relation"] not in pattern.required_relations:
                continue
            required_names = {_normalize_file_name(value) for value in pattern.required_file_names}
            if required_names and observation["file_name"] not in required_names:
                continue
            required_prefixes = [_normalize_path(value) for value in pattern.required_path_prefixes]
            if required_prefixes and not any(_path_matches_prefix(observation["file_path"], prefix) for prefix in required_prefixes):
                continue
            required_suffixes = [_normalize_path(value) for value in pattern.required_path_suffixes]
            if required_suffixes and not any(observation["file_path"].endswith(suffix) for suffix in required_suffixes):
                continue
            details = [f"relation={observation['relation']}"]
            if required_names:
                details.append(f"file_names={','.join(sorted(required_names))}")
            if required_prefixes:
                details.append(f"path_prefixes={','.join(required_prefixes)}")
            if required_suffixes:
                details.append(f"path_suffixes={','.join(required_suffixes)}")
            matches.append(f"{observation['observation_id']}|{'|'.join(details)}")
    return list(dict.fromkeys(matches))


def _match_cidrs(ips: set[str], cidrs: list[str]) -> list[str]:
    networks = [ipaddress.ip_network(value, strict=False) for value in cidrs]
    matches: list[str] = []
    for value in sorted(ips):
        address = ipaddress.ip_address(value)
        if any(address.version == network.version and address in network for network in networks):
            matches.append(value)
    return matches


def _fact_context_item(
    profile: TenantKnowledgeProfile,
    fact: TenantKnowledgeFact,
    matches: dict[str, list[str]],
) -> AnalysisContextCatalogItem:
    projection = {
        "profile_id": profile.profile_id,
        "profile_version": profile.version,
        "fact_id": fact.fact_id,
        "statement": fact.statement,
        "matches": matches,
    }
    projection_hash = stable_hash(projection)
    match_summary = "; ".join(f"{key}={','.join(values)}" for key, values in sorted(matches.items()))
    metadata = {
        "profile_id": profile.profile_id,
        "profile_version": profile.version,
        "fact_id": fact.fact_id,
        "fact_kind": fact.kind.value,
        "source_ref": fact.source_ref,
        "review_status": profile.review_status,
        "matched_values": matches,
        "decision_authority": "none",
    }
    if fact.network_scope_membership is not None:
        metadata["network_scope_membership"] = fact.network_scope_membership
    return AnalysisContextCatalogItem(
        context_ref=f"C-{projection_hash[:12].upper()}",
        kind=AnalysisContextReferenceKind.GOVERNED_CONTEXT,
        label=fact.label,
        source_id=f"{profile.profile_id}@{profile.version}:{fact.fact_id}",
        summary=f"{fact.statement}\nCurrent-alert match: {match_summary}"[:4000],
        content_hash=projection_hash,
        metadata=metadata,
    )


def _normalize_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        return ""


def _normalize_domain(value: str) -> str:
    text = str(value).strip().casefold().rstrip(".")
    if "://" in text:
        text = text.split("://", 1)[1]
    return text.split("/", 1)[0].split(":", 1)[0]


def _normalize_host(value: str) -> str:
    return str(value).strip().casefold()[:512]


def _normalize_process_name(value: str) -> str:
    text = str(value).strip().replace("\\", "/")
    return text.rsplit("/", 1)[-1].casefold()[:512]


def _normalize_file_name(value: str) -> str:
    text = str(value).strip().replace("\\", "/")
    return text.rsplit("/", 1)[-1].casefold()[:512]


def _normalize_path(value: str) -> str:
    text = str(value).strip().replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    return text.rstrip("/").casefold()[:2000]


def _normalize_command_line(value: str) -> str:
    text = str(value).strip().replace("\\", "/").casefold()
    text = re.sub(r"(?<!:)/{2,}", "/", text)
    return " ".join(text.split())[:4000]


def _normalize_account(value: str) -> str:
    return str(value).strip()[:512]


def _normalize_uri(value: str) -> str:
    text = str(value).strip()
    if not text:
        return ""
    parsed = urlsplit(text if "://" in text else f"https://placeholder.invalid/{text.lstrip('/')}")
    path = parsed.path or "/"
    return f"/{path.lstrip('/')}"[:2000]


def _path_matches_prefix(value: str, prefix: str) -> bool:
    return bool(prefix) and (value == prefix or value.startswith(f"{prefix}/"))


def _uri_matches_prefix(value: str, prefix: str) -> bool:
    normalized_prefix = prefix.rstrip("/") or "/"
    return value == normalized_prefix or value.startswith(f"{normalized_prefix}/")


def _domain_matches_suffix(domain: str, suffix: str) -> bool:
    normalized = suffix.strip().casefold().lstrip("*.").rstrip(".")
    return domain == normalized or domain.endswith(f".{normalized}")


def _dedupe_context_items(items: list[AnalysisContextCatalogItem]) -> list[AnalysisContextCatalogItem]:
    by_ref = {item.context_ref: item for item in items}
    return sorted(by_ref.values(), key=lambda item: item.context_ref)


__all__ = [
    "CompositeAnalysisRequestEnricher",
    "TenantKnowledgeAnalysisRequestEnricher",
    "load_tenant_knowledge_profile",
]
