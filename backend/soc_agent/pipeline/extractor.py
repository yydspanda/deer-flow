"""Code-first entity extraction for Phase 1."""

from __future__ import annotations

import hashlib
import ipaddress
import re

from soc_agent.contracts import AlertInput, EntityKind, EntityMention, ExtractedEntities

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
DOMAIN_RE = re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b")


def extract_entities(alert: AlertInput) -> ExtractedEntities:
    warnings: list[str] = []

    network = alert.entities.network
    process = alert.entities.process
    user = alert.entities.user
    host = alert.entities.host
    http = alert.entities.http

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
    _add_ip_mention(
        mentions,
        http.x_forwarded_for,
        role="x_forwarded_for",
        evidence_path="entities.http.x_forwarded_for",
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
    for value in DOMAIN_RE.findall(process.command_line or ""):
        _add_mention(
            mentions,
            EntityKind.DOMAIN,
            value,
            role="process_command_line_domain",
            evidence_path="entities.process.command_line",
        )
    for value in DOMAIN_RE.findall(network.url or ""):
        _add_mention(
            mentions,
            EntityKind.DOMAIN,
            value,
            role="network_url_domain",
            evidence_path="entities.network.url",
        )
    for value in DOMAIN_RE.findall(http.url or ""):
        _add_mention(
            mentions,
            EntityKind.DOMAIN,
            value,
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


def _normalize_entity_value(kind: EntityKind, value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if kind in {EntityKind.DOMAIN, EntityKind.URL}:
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
