"""Code-first entity extraction for Phase 1."""

from __future__ import annotations

import ipaddress
import re

from soc_agent.contracts import AlertInput, ExtractedEntities

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
DOMAIN_RE = re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b")


def extract_entities(alert: AlertInput) -> ExtractedEntities:
    warnings: list[str] = []

    ips = _valid_ips(
        [
            value
            for value in [
                alert.source_ip,
                alert.destination_ip,
                *IP_RE.findall(alert.command_line or ""),
            ]
            if value
        ]
    )
    domains = _dedupe(
        [
            value
            for value in [
                alert.domain,
                *DOMAIN_RE.findall(alert.command_line or ""),
            ]
            if value
        ]
    )
    processes = _dedupe([value for value in [alert.process_name] if value])
    users = _dedupe([value for value in [alert.username] if value])
    hosts = _dedupe([value for value in [alert.host_name] if value])
    rules = _dedupe([value for value in [alert.rule_name] if value])

    if not alert.rule_name:
        warnings.append("missing optional field: rule_name")
    if not ips:
        warnings.append("no valid IP entity extracted")
    if not processes:
        warnings.append("no process entity extracted")

    return ExtractedEntities(
        ips=ips,
        domains=domains,
        processes=processes,
        users=users,
        hosts=hosts,
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
