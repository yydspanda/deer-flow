"""Shared qualification rules for investigation evidence used by findings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from soc_agent.contracts import InvestigationEvidence


def evidence_is_mocked(evidence: InvestigationEvidence) -> bool:
    """Recognize explicit mock provenance, including persisted legacy payloads."""

    return evidence.mocked or _contains_mock_marker(evidence.result_payload)


def evidence_result_payload(evidence: InvestigationEvidence) -> Mapping[str, Any]:
    """Return the typed provider result from direct or MCP action evidence."""

    mcp_result = evidence.result_payload.get("mcp_result")
    if isinstance(mcp_result, Mapping):
        return mcp_result
    return evidence.result_payload


def successful_evidence(
    evidence: Sequence[InvestigationEvidence],
    *,
    include_mocked: bool = False,
) -> list[InvestigationEvidence]:
    """Return successful evidence eligible for semantic finding calculations."""

    return [item for item in evidence if item.status == "success" and (include_mocked or not evidence_is_mocked(item))]


def successful_evidence_routes(
    evidence: Sequence[InvestigationEvidence],
    *,
    include_mocked: bool = False,
) -> list[str]:
    routes: list[str] = []
    for item in successful_evidence(evidence, include_mocked=include_mocked):
        routes.extend([item.route, item.action])
    return sorted(set(routes))


def _contains_mock_marker(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("mocked") is True:
            return True
        return any(_contains_mock_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_mock_marker(item) for item in value)
    return False


__all__ = [
    "evidence_is_mocked",
    "evidence_result_payload",
    "successful_evidence",
    "successful_evidence_routes",
]
