"""Code-first entity extraction for Phase 1."""

from __future__ import annotations

import ipaddress
import re

from soc_agent.contracts import AlertInput, ExtractedEntities

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
DOMAIN_RE = re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b")


def extract_entities(alert: AlertInput) -> ExtractedEntities:
    warnings: list[str] = []

    network = alert.entities.network
    process = alert.entities.process
    user = alert.entities.user
    host = alert.entities.host
    http = alert.entities.http

    ips = _valid_ips(
        [
            value
            for value in [
                network.source_ip,
                network.destination_ip,
                http.x_forwarded_for,
                *IP_RE.findall(process.command_line or ""),
                *IP_RE.findall(process.parent_command_line or ""),
            ]
            if value
        ]
    )
    domains = _dedupe(
        [
            value
            for value in [
                network.domain,
                http.host,
                *DOMAIN_RE.findall(process.command_line or ""),
                *DOMAIN_RE.findall(network.url or ""),
                *DOMAIN_RE.findall(http.url or ""),
            ]
            if value
        ]
    )
    urls = _dedupe([value for value in [network.url, http.url] if value])
    processes = _dedupe(
        [
            value
            for value in [
                process.process_name,
                process.parent_process_name,
            ]
            if value
        ]
    )
    users = _dedupe([value for value in [user.username, user.src_user, user.dst_user] if value])
    hosts = _dedupe([value for value in [host.host_name, host.asset_id, host.asset_group] if value])
    rule_codes = _dedupe([value for value in [alert.detection.rule_code] if value])
    rule_names = _dedupe([value for value in [alert.detection.rule_name] if value])
    rules = _dedupe([*rule_codes, *rule_names, alert.detection.detection_key])

    if not alert.detection.rule_name:
        warnings.append("missing optional field: rule_name")
    if not alert.detection.rule_code:
        warnings.append("missing optional field: rule_code")
    if not ips:
        warnings.append("no valid IP entity extracted")
    if not processes:
        warnings.append("no process entity extracted")

    return ExtractedEntities(
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


def _valid_ips(values: list[str]) -> list[str]:
    valid: list[str] = []
    for value in values:
        try:
            valid.append(str(ipaddress.ip_address(value)))
        except ValueError:
            continue
    return _dedupe(valid)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
