"""Code-first entity extraction for the SOC Runtime."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from urllib.parse import urlsplit

from soc_agent.contracts import AlertInput, EntityKind, EntityMention, ExtractedEntities

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
DOMAIN_RE = re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b")
FILE_HASH_RE = re.compile(r"^(?:[0-9A-Fa-f]{32}|[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$")
_FILE_LIKE_SUFFIXES = frozenset(
    {
        "asp",
        "aspx",
        "css",
        "dll",
        "exe",
        "html",
        "jar",
        "js",
        "json",
        "jsp",
        "log",
        "php",
        "py",
        "sh",
        "so",
        "txt",
        "xml",
        "yaml",
        "yml",
    }
)


def extract_entities(alert: AlertInput) -> ExtractedEntities:
    warnings: list[str] = []

    network = alert.entities.network
    process = alert.entities.process
    user = alert.entities.user
    host = alert.entities.host
    http = alert.entities.http
    threat = alert.entities.threat

    mentions: list[EntityMention] = []
    _add_ip_mention(
        mentions,
        network.source_ip,
        role="source_ip",
        evidence_path="entities.network.source_ip",
    )
    _add_ip_mention(
        mentions,
        network.destination_ip,
        role="destination_ip",
        evidence_path="entities.network.destination_ip",
    )
    for value in re.split(r"\s*[,;]\s*", http.x_forwarded_for or ""):
        _add_ip_mention(
            mentions,
            value,
            role="x_forwarded_for",
            evidence_path="entities.http.x_forwarded_for",
        )
    for value in host.ip_addresses:
        _add_ip_mention(
            mentions,
            value,
            role="host_ip",
            evidence_path="entities.host.ip_addresses",
        )
    for index, value in enumerate(threat.iocs):
        _add_ioc_mention(
            mentions,
            value,
            evidence_path=f"entities.threat.iocs[{index}]",
        )
    for value in IP_RE.findall(process.command_line or ""):
        _add_ip_mention(
            mentions,
            value,
            role="process_command_line_ip",
            evidence_path="entities.process.command_line",
        )
    for value in IP_RE.findall(process.parent_command_line or ""):
        _add_ip_mention(
            mentions,
            value,
            role="parent_process_command_line_ip",
            evidence_path="entities.process.parent_command_line",
        )

    _add_mention(
        mentions,
        EntityKind.DOMAIN,
        network.domain,
        role="network_domain",
        evidence_path="entities.network.domain",
    )
    _add_mention(
        mentions,
        EntityKind.DOMAIN,
        http.host,
        role="http_host",
        evidence_path="entities.http.host",
    )
    _add_domains_from_text(
        mentions,
        process.command_line,
        role="process_command_line_domain",
        evidence_path="entities.process.command_line",
    )
    _add_url_domain(
        mentions,
        network.url,
        role="network_url_domain",
        evidence_path="entities.network.url",
    )
    _add_url_domain(
        mentions,
        http.url,
        role="http_url_domain",
        evidence_path="entities.http.url",
    )

    _add_mention(mentions, EntityKind.URL, network.url, role="network_url", evidence_path="entities.network.url")
    _add_mention(mentions, EntityKind.URL, http.url, role="http_url", evidence_path="entities.http.url")
    _add_mention(
        mentions,
        EntityKind.PROCESS,
        process.process_name,
        role="process_name",
        evidence_path="entities.process.process_name",
    )
    _add_mention(
        mentions,
        EntityKind.PROCESS,
        process.parent_process_name,
        role="parent_process_name",
        evidence_path="entities.process.parent_process_name",
    )
    _add_mention(
        mentions,
        EntityKind.FILE_HASH,
        process.sha256,
        role="process_sha256",
        evidence_path="entities.process.sha256",
    )
    _add_mention(
        mentions,
        EntityKind.FILE_HASH,
        process.md5,
        role="process_md5",
        evidence_path="entities.process.md5",
    )
    for observation_index, observation in enumerate(process.observations):
        _add_mention(
            mentions,
            EntityKind.HOST,
            observation.host_name,
            role="process_observation_host",
            evidence_path=f"entities.process.observations[{observation_index}].host_name",
        )
        for node_index, node in enumerate(observation.nodes):
            node_path = f"entities.process.observations[{observation_index}].nodes[{node_index}]"
            _add_mention(
                mentions,
                EntityKind.PROCESS,
                node.process_name,
                role="observed_process_name",
                evidence_path=f"{node_path}.process_name",
            )
            _add_mention(
                mentions,
                EntityKind.USER,
                node.username,
                role="observed_process_user",
                evidence_path=f"{node_path}.username",
            )
            _add_mention(
                mentions,
                EntityKind.FILE_HASH,
                node.sha256,
                role="observed_process_sha256",
                evidence_path=f"{node_path}.sha256",
            )
            _add_mention(
                mentions,
                EntityKind.FILE_HASH,
                node.md5,
                role="observed_process_md5",
                evidence_path=f"{node_path}.md5",
            )
    _add_mention(mentions, EntityKind.USER, user.username, role="username", evidence_path="entities.user.username")
    _add_mention(mentions, EntityKind.USER, user.src_user, role="src_user", evidence_path="entities.user.src_user")
    _add_mention(mentions, EntityKind.USER, user.dst_user, role="dst_user", evidence_path="entities.user.dst_user")
    _add_mention(mentions, EntityKind.USER, user.user_id, role="user_id", evidence_path="entities.user.user_id")
    _add_mention(mentions, EntityKind.USER, user.um_account, role="um_account", evidence_path="entities.user.um_account")
    _add_mention(mentions, EntityKind.HOST, host.host_name, role="host_name", evidence_path="entities.host.host_name")
    _add_mention(mentions, EntityKind.ASSET, host.asset_id, role="asset_id", evidence_path="entities.host.asset_id")
    _add_mention(mentions, EntityKind.ASSET, host.asset_group, role="asset_group", evidence_path="entities.host.asset_group")
    _add_mention(
        mentions,
        EntityKind.FILE_HASH,
        alert.entities.file.sha256,
        role="sha256",
        evidence_path="entities.file.sha256",
    )
    _add_mention(mentions, EntityKind.FILE_HASH, alert.entities.file.sha1, role="sha1", evidence_path="entities.file.sha1")
    _add_mention(mentions, EntityKind.FILE_HASH, alert.entities.file.md5, role="md5", evidence_path="entities.file.md5")
    for observation_index, observation in enumerate(alert.entities.file.observations):
        observation_path = f"entities.file.observations[{observation_index}]"
        _add_mention(
            mentions,
            EntityKind.FILE_HASH,
            observation.sha256,
            role=f"{observation.relation}_sha256",
            evidence_path=f"{observation_path}.sha256",
        )
        _add_mention(
            mentions,
            EntityKind.FILE_HASH,
            observation.sha1,
            role=f"{observation.relation}_sha1",
            evidence_path=f"{observation_path}.sha1",
        )
        _add_mention(
            mentions,
            EntityKind.FILE_HASH,
            observation.md5,
            role=f"{observation.relation}_md5",
            evidence_path=f"{observation_path}.md5",
        )
    _add_mention(
        mentions,
        EntityKind.RULE_CODE,
        alert.detection.rule_code,
        role="rule_code",
        evidence_path="detection.rule_code",
    )
    _add_mention(
        mentions,
        EntityKind.RULE_NAME,
        alert.detection.rule_name,
        role="rule_name",
        evidence_path="detection.rule_name",
    )
    _add_mention(
        mentions,
        EntityKind.RULE,
        alert.detection.detection_key,
        role="detection_key",
        evidence_path="detection.detection_key",
    )
    for value in alert.classification.tactic:
        _add_mention(mentions, EntityKind.MITRE, value, role="tactic", evidence_path="classification.tactic")
    for value in alert.classification.technique:
        _add_mention(mentions, EntityKind.MITRE, value, role="technique", evidence_path="classification.technique")

    mentions = _dedupe_mentions(mentions)
    ips = _values_by_kind(mentions, EntityKind.IP)
    domains = _values_by_kind(mentions, EntityKind.DOMAIN)
    urls = _values_by_kind(mentions, EntityKind.URL)
    processes = _values_by_kind(mentions, EntityKind.PROCESS)
    users = _values_by_kind(mentions, EntityKind.USER)
    hosts = _dedupe([*_values_by_kind(mentions, EntityKind.HOST), *_values_by_kind(mentions, EntityKind.ASSET)])
    rule_codes = _values_by_kind(mentions, EntityKind.RULE_CODE)
    rule_names = _values_by_kind(mentions, EntityKind.RULE_NAME)
    rules = _dedupe([*rule_codes, *rule_names, *_values_by_kind(mentions, EntityKind.RULE)])

    if not alert.detection.rule_name:
        warnings.append("missing optional field: rule_name")
    if not alert.detection.rule_code:
        warnings.append("missing optional field: rule_code")
    if not ips:
        warnings.append("no valid IP entity extracted")
    if not processes:
        warnings.append("no process entity extracted")

    return ExtractedEntities(
        mentions=mentions,
        ips=ips,
        domains=domains,
        urls=urls,
        processes=processes,
        users=users,
        hosts=hosts,
        rule_codes=rule_codes,
        rule_names=rule_names,
        rules=rules,
        warnings=warnings,
    )


def _add_ip_mention(
    mentions: list[EntityMention],
    value: str | None,
    *,
    role: str,
    evidence_path: str,
) -> None:
    if not value:
        return
    try:
        normalized = str(ipaddress.ip_address(value))
    except ValueError:
        return
    _add_mention(mentions, EntityKind.IP, normalized, role=role, evidence_path=evidence_path)


def _add_ioc_mention(
    mentions: list[EntityMention],
    value: str,
    *,
    evidence_path: str,
) -> None:
    try:
        normalized_ip = str(ipaddress.ip_address(value))
    except ValueError:
        normalized_ip = None
    if normalized_ip is not None:
        _add_mention(
            mentions,
            EntityKind.IP,
            normalized_ip,
            role="threat_ioc",
            evidence_path=evidence_path,
        )
        return

    if FILE_HASH_RE.fullmatch(value):
        _add_mention(
            mentions,
            EntityKind.FILE_HASH,
            value,
            role="threat_ioc",
            evidence_path=evidence_path,
        )
        return

    parsed = urlsplit(value)
    if parsed.scheme and parsed.hostname:
        _add_mention(
            mentions,
            EntityKind.URL,
            value,
            role="threat_ioc",
            evidence_path=evidence_path,
        )
        _add_mention(
            mentions,
            EntityKind.DOMAIN,
            parsed.hostname,
            role="threat_ioc_domain",
            evidence_path=evidence_path,
        )
        return

    _add_mention(
        mentions,
        EntityKind.DOMAIN,
        value,
        role="threat_ioc",
        evidence_path=evidence_path,
    )


def _add_mention(
    mentions: list[EntityMention],
    kind: EntityKind,
    value: str | None,
    *,
    role: str,
    evidence_path: str,
) -> None:
    normalized = _normalize_entity_value(kind, value)
    if not normalized:
        return
    mentions.append(
        EntityMention(
            kind=kind,
            value=normalized,
            key=_entity_key(kind, normalized),
            role=role,
            evidence_path=evidence_path,
        )
    )


def _add_url_domain(
    mentions: list[EntityMention],
    value: str | None,
    *,
    role: str,
    evidence_path: str,
) -> None:
    if not value:
        return
    parsed = urlsplit(value)
    if not parsed.scheme and not value.startswith("//"):
        return
    _add_mention(
        mentions,
        EntityKind.DOMAIN,
        parsed.hostname,
        role=role,
        evidence_path=evidence_path,
    )


def _add_domains_from_text(
    mentions: list[EntityMention],
    value: str | None,
    *,
    role: str,
    evidence_path: str,
) -> None:
    for candidate in DOMAIN_RE.findall(value or ""):
        if candidate.rsplit(".", 1)[-1].lower() in _FILE_LIKE_SUFFIXES:
            continue
        _add_mention(
            mentions,
            EntityKind.DOMAIN,
            candidate,
            role=role,
            evidence_path=evidence_path,
        )


def _normalize_entity_value(kind: EntityKind, value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if kind is EntityKind.DOMAIN:
        candidate = normalized.lower().rstrip(".")
        if ":" in candidate and candidate.count(":") == 1:
            candidate = candidate.split(":", 1)[0]
        if not DOMAIN_RE.fullmatch(candidate):
            return None
        if candidate.rsplit(".", 1)[-1] in _FILE_LIKE_SUFFIXES:
            return None
        return candidate
    if kind is EntityKind.URL:
        return normalized.lower()
    if kind is EntityKind.FILE_HASH:
        return normalized.upper()
    return normalized


def _entity_key(kind: EntityKind, value: str) -> str:
    if kind is EntityKind.RULE:
        return f"rule:{_short_hash(value)}"
    return f"{kind.value}:{value}"


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _dedupe_mentions(mentions: list[EntityMention]) -> list[EntityMention]:
    seen: set[tuple[str, str, str | None]] = set()
    result: list[EntityMention] = []
    for mention in mentions:
        key = (mention.kind.value, mention.value, mention.role)
        if key not in seen:
            seen.add(key)
            result.append(mention)
    return result


def _values_by_kind(mentions: list[EntityMention], kind: EntityKind) -> list[str]:
    return _dedupe([mention.value for mention in mentions if mention.kind is kind])


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
